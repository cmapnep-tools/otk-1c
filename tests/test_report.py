"""Сводка и вердикт; коммит контрольных дефектов без изменения рабочего дерева."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c import model, public
from otk_1c.extras import controls
from otk_1c.gate import output, report, report_html

PLAN = {"base": "a", "head": "b", "source": "src/cf", "modules": [{"path": "CommonModules/М/Ext/Module.bsl"}],
        "queries": [{"id": "q1", "module": "CommonModules/М/Ext/Module.bsl", "line": 5, "complete": False,
                     "text": "…"}],
        "removedModules": [], "metadata": []}


class TestReport(unittest.TestCase):
    def test_public_summary_allowlist_and_redaction(self):
        summary = {"schema": "otk.summary.v1", "version": "1.0", "code": 1,
                   "findings": [{"severity": "error", "step": "static", "rule": "x", "file": "m.bsl",
                                 "line": 2, "column": 1, "message": 'Pwd="hunter2"', "text": "Х = 1;",
                                 "moduleText": "полный модуль", "sources": [{"step": "static", "rule": "x",
                                  "message": 'Srvr="host";Ref="demo";Usr="admin";Pwd="hunter2";'}]}],
                   "notChecked": [], "uncovered": [],
                   "notes": ["password=secret", "OTK_PASSWORD=another-secret", '{"password":"json-secret"}',
                             r'{"password":"pre\"post"}'],
                   "infra": [],
                   "scenarios": {"selected": 1, "private": "вне вложенного контракта"},
                   "smoke": {"opened": 1, "private": "вне вложенного контракта"},
                   "timings": {"collect": 1.0, "private": "вне вложенного контракта"},
                   "secret": "вне контракта"}
        public = report.public_summary(summary)
        encoded = str(public)
        self.assertNotIn("полный модуль", encoded)
        self.assertNotIn("вне контракта", encoded)
        self.assertNotIn("вне вложенного контракта", encoded)
        self.assertNotIn("hunter2", encoded)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("json-secret", encoded)
        self.assertIn('{"password":"[скрыто]"}', encoded)
        self.assertIn("[строка соединения скрыта]", encoded)
        self.assertEqual(public["findings"][0]["text"], "Х = 1;")

    def test_public_text_keeps_paths_and_identifiers(self):
        """Маскирование не съедает пути Linux и хвосты идентификаторов: секрет — только отдельный ключ."""
        for text in ("C:/src/cf/CommonModules/М/Ext/Module.bsl", "A = maxTokens / 2;", 'userToken: "abc"',
                     "/Fitness"):
            with self.subTest(text=text):
                self.assertEqual(public.public_text(text), text)
        self.assertEqual(public.public_text('{"token":"abc"}'), '{"token":"[скрыто]"}')
        self.assertEqual(public.public_text("db_password=abc"), "db_password=[скрыто]")

    def test_public_text_masks_connection_aliases(self):
        for text in ('--ib "/F/home/ib"', r"/Sserver\base", "/FC:/1C/Копии/ERP"):
            with self.subTest(text=text):
                self.assertIn("[база скрыта]", public.public_text(text))

    def test_formatters_redact_raw_summary(self):
        summary = report.build(PLAN, {"findings": [{"check": "x", "severity": "error", "module": "m.bsl",
                                                    "line": 1, "message": 'Pwd="secret"'}]}, None, {})
        summary["notes"].append('Srvr="host";Ref="demo";Usr="admin";Pwd="secret";')
        outputs = [report.markdown(summary), report.brief(summary), str(report.problems(summary, "repo")),
                   report.junit(summary), str(report.sarif(summary, "repo"))]
        for rendered in outputs:
            self.assertNotIn("secret", rendered)
            self.assertNotIn("host", rendered)

    def test_error_verdict_and_problems(self):
        static = {"findings": [{"check": "metadata-ref", "severity": "error",
                                "module": "CommonModules/М/Ext/Module.bsl",
                                "line": 7, "message": "Объект не найден: Справочники.Х"}]}
        run = {"results": [{"executor": "com", "kind": "probe", "module": "CommonModules/М/Ext/Module.bsl",
                            "owner": "ОбщийМодуль.М", "context": "ExternalConnection", "outcome": "compile-error",
                            "line": 9, "message": "ОбщийМодуль.М.Модуль(9,3): Ожидается ключевое слово 'КонецЕсли'"}],
               "notes": []}
        s = report.build(PLAN, static, run, {"collect": 1})
        self.assertEqual((s["code"], s["verdict"]), (1, "есть ошибки — не отправлять"))
        lines = report.problems(s, Path("D:/r"))
        tail = "(7,1): error metadata-ref: Объект не найден: Справочники.Х [найдено: статика (metadata-ref)]"
        self.assertTrue(lines[0].endswith("CommonModules\\М\\Ext\\Module.bsl" + tail)
                        or lines[0].endswith("CommonModules/М/Ext/Module.bsl" + tail))
        self.assertIn("(9,3): error probe/compile-error", lines[1])
        self.assertEqual(len(s["notChecked"]), 1)          # неполный запрос
        self.assertIn('failures="2"', report.junit(s))

    def test_same_finding_once_with_count(self):
        """Одна ошибка одного места (запрос разбирается несколько раз) — одна находка со счётчиком."""
        f = {"check": "metadata-ref", "severity": "error", "module": "CommonModules/М/Ext/Module.bsl",
             "line": 7, "message": "ЗНАЧЕНИЕ: нет объекта Перечисление.Х"}
        s = report.build(PLAN, {"findings": [dict(f), dict(f), dict(f), {**f, "line": 8}]}, None, {"collect": 1})
        self.assertEqual([(x["line"], x.get("count")) for x in s["findings"]], [(7, 3), (8, None)])
        self.assertIn("×3", report.markdown(s))

    def test_platform_duplicates(self):
        """BSL LS не дублирует платформу, своя статика и платформа в одной строке — одна находка."""
        mod = "CommonModules/М/Ext/Module.bsl"
        plan = {**PLAN, "queries": [{"id": "q1", "module": mod, "line": 20, "complete": True,
                                     "text": "ВЫБРАТЬ\nПоле\nИЗ Т"}]}

        def st(check, line, severity="error"):
            return {"check": check, "severity": severity, "module": mod, "line": line, "message": check}
        static = {"findings": [st("bslls:ParseError", 5), st("bslls:QueryParseError", 21, "warning"),
                               st("bslls:QueryParseError", 40, "warning"), st("platform-api", 12),
                               st("bslls:SelfAssign", 30, "warning")]}

        def rs(kind, line, outcome="compile-error", **kw):
            return {"executor": "extcheck" if kind == "extcheck" else "com", "kind": kind, "module": mod,
                    "context": "Server", "outcome": outcome, "line": line, "message": f"{kind} {line}", **kw}
        run = {"results": [rs("probe", 6), rs("extcheck", 12), rs("query", 20, "ok", query="q1")], "notes": []}
        findings = report.build(plan, static, run, {})["findings"]
        self.assertEqual(sorted((f["rule"], f["line"]) for f in findings),
                         [("bslls:QueryParseError", 40), ("bslls:SelfAssign", 30), ("platform-api", 12),
                          ("probe/compile-error", 6)])
        api = next(f for f in findings if f["rule"] == "platform-api")
        self.assertEqual([(x["step"], x["rule"]) for x in api["sources"]],
                         [("static", "platform-api"), ("run:extcheck", "extcheck/compile-error")])
        probe = next(f for f in findings if f["rule"] == "probe/compile-error")    # BSL LS ParseError в строке 5
        self.assertEqual([x["rule"] for x in probe["sources"]], ["probe/compile-error", "bslls:ParseError"])
        md = report.markdown(report.build(plan, static, run, {}))
        self.assertIn("найдено: статика (platform-api), Конфигуратор (временное расширение)", md)
        self.assertIn("отброшено: 1", md)                                       # QueryParseError в строке 21
        # без проб — все находки статики на месте
        self.assertEqual(len(report.build(plan, static, None, {})["findings"]), 5)

    def test_sources_merge_contexts(self):
        """Одна ошибка одного места в двух контекстах — одна находка, оба источника."""
        mod = "CommonModules/М/Ext/Module.bsl"
        run = {"results": [{"executor": ex, "kind": "probe", "module": mod, "owner": "ОбщийМодуль.М", "context": c,
                            "outcome": "compile-error", "line": 3, "message": "Переменная не определена (Х)"}
                           for ex, c in (("com", "ExternalConnection"), ("yaxunit", "Server"))], "notes": []}
        f, = report.build(PLAN, {"findings": []}, run, {})["findings"]
        self.assertEqual([(s["step"], s["context"]) for s in f["sources"]],
                         [("run:com", "ExternalConnection"), ("run:yaxunit", "Server")])
        self.assertIn("найдено: проба COM (внешнее соединение), проба YAxUnit (сервер)", report.problems(
            {"findings": [f]}, "repo")[0])

    def test_compile_error_com_and_extcheck(self):
        """Ошибка компиляции одной строки от COM (префикс места) и extcheck (суффикс режимов) — одна находка;
        разные ошибки одной строки — разные."""
        mod = "CommonModules/М/Ext/Module.bsl"

        def r(ex, kind, ctx, msg):
            return {"executor": ex, "kind": kind, "module": mod, "owner": "ОбщийМодуль.М", "context": ctx,
                    "outcome": "compile-error", "line": 189, "message": msg}
        run = {"results": [
            r("com", "probe", "Server", "demo ОбщийМодуль.М.Модуль(189,6): Переменная не определена (Х)"),
            r("extcheck", "extcheck", "Designer", "Переменная не определена (Х) (Внешнее соединение, Сервер)"),
            r("yaxunit", "probe", "ThinClient", "ОбщийМодуль.М.Модуль(189,6): Переменная не определена (Y)")],
            "notes": []}
        found = report.build(PLAN, {"findings": []}, run, {})["findings"]
        self.assertEqual([[s["step"] for s in f["sources"]] for f in found],
                         [["run:com", "run:extcheck"], ["run:yaxunit"]])
        self.assertTrue(report.same_compile_error("Ожидается выражение", "М.Модуль(1,1): Ожидается выражение"))
        self.assertFalse(report.same_compile_error("Переменная не определена (Х)", "Переменная не определена (Y)"))
        # исход выполнения (runtime-error) так не склеивается
        rt = [{**x, "outcome": "runtime-error"} for x in run["results"][:2]]
        self.assertEqual(len(report.build(PLAN, {"findings": []}, {"results": rt, "notes": []}, {})["findings"]), 2)

    def test_object_method_and_code(self):
        """Объект, метод, место для Конфигуратора и строка кода проверенной редакции."""
        mod, form = "CommonModules/М/Ext/Module.bsl", "Catalogs/Т/Forms/Ф/Ext/Form/Module.bsl"
        plan = {**PLAN, "queries": [], "modules": [
            {"path": mod, "kind": "CommonModule", "owner": "ОбщийМодуль.М"},
            {"path": form, "kind": "FormModule", "owner": "Справочник.Т.Форма.Ф"}]}
        texts = {mod: "Процедура А() Экспорт\n\tНет();\nКонецПроцедуры\n\nХ = 1;\n",
                 form: "&НаКлиенте\nПроцедура Б()\n\tКомандаСистемы(\"x\");\nКонецПроцедуры\n"}
        static = {"findings": [{"check": "metadata-ref", "severity": "error", "module": mod, "line": 2,
                                "message": "нет"}, {"check": "x", "severity": "error", "module": mod, "line": 5,
                                                    "message": "тело"},
                               {"check": "y", "severity": "error", "module": form, "line": 3, "message": "веб"}]}
        s = report.build(plan, static, None, {}, read=texts.get)
        got = [(f["object"], f.get("method"), f["place"], f.get("text")) for f in s["findings"]]
        self.assertEqual(got, [("ОбщийМодуль.М", "А", "ОбщийМодуль.М.Модуль(2)", "Нет();"),
                               ("ОбщийМодуль.М", None, "ОбщийМодуль.М.Модуль(5)", "Х = 1;"),
                               ("Справочник.Т.Форма.Ф", "Б", "Справочник.Т.Форма.Ф.Форма(3)",
                                'КомандаСистемы("x");')])
        md = report.markdown(s)
        self.assertIn("### ОбщийМодуль.М\n- `ОбщийМодуль.М.Модуль(2)` А — нет", md)
        self.assertIn("  `Нет();`", md)
        self.assertIn(f"`src/cf/{mod}:2`", md)
        self.assertIn("[ОбщийМодуль.М.А; найдено", report.problems(s, "repo")[0])
        # без текста редакции — объект и место есть, метода и строки нет
        f = report.build(plan, static, None, {})["findings"][0]
        self.assertEqual((f["place"], f.get("method"), f.get("text")), ("ОбщийМодуль.М.Модуль(2)", None, None))

    def test_finding_without_file_or_line(self):
        """Находка без модуля (упал сценарий вне плана) не роняет markdown/JUnit/Problems; находка без
        строки — место модуля без номера, без метода и строки кода."""
        scen = {"executor": "scenarios", "kind": "scenario", "module": None, "owner": "ТестМодуль.Тест",
                "context": "Server", "outcome": "failed", "message": "упал", "line": None}
        warn = [{"executor": "com", "kind": "query", "module": None, "owner": None, "context": "Server",
                 "outcome": "runtime-error", "message": "предупреждение", "line": None, "query": f"q{i}"}
                for i in range(4)]
        mod = "CommonModules/М/Ext/Module.bsl"
        plan = {**PLAN, "modules": [{"path": mod, "kind": "CommonModule", "owner": "ОбщийМодуль.М"}]}
        sess = {"executor": "com", "kind": "session", "module": mod, "owner": "ОбщийМодуль.М",
                "context": "ExternalConnection", "outcome": "compile-error", "message": "сеанс", "line": None}
        s = report.build(plan, {"findings": []}, {"results": [scen, sess] + warn, "notes": []}, {},
                         read=lambda rel: "Процедура А()\nКонецПроцедуры\n")
        self.assertIn("`без модуля`", report.markdown(s))
        self.assertIn('name="без модуля scenario/failed"', report.junit(s))
        self.assertEqual(len(report.problems(s, "repo")), 6)
        f = next(x for x in s["findings"] if x["rule"] == "session/compile-error")
        self.assertEqual((f["place"], f.get("method"), f.get("text")), ("ОбщийМодуль.М.Модуль", None, None))
        self.assertNotIn("object", next(x for x in s["findings"] if x["rule"] == "scenario/failed"))

    def test_place_of_kinds(self):
        self.assertEqual(report.place_of({"kind": "SessionModule", "owner": "SessionModule"}, 5),
                         ("Конфигурация", "Конфигурация.МодульСеанса(5)"))
        self.assertEqual(report.place_of({"kind": "CommandModule", "owner": "Справочник.Т.Команда.К"}, 2)[1],
                         "Справочник.Т.Команда.К.МодульКоманды(2)")
        self.assertEqual(report.place_of({"kind": "RecordSetModule", "owner": "РегистрСведений.Р"}, 1)[1],
                         "РегистрСведений.Р.МодульНабораЗаписей(1)")
        self.assertEqual(report.place_of({"kind": "HTTPService", "owner": "HTTPСервис.С"}, 3)[1],
                         "HTTPСервис.С.Модуль(3)")
        self.assertEqual(report.place_of({"kind": "Unknown", "owner": "x/y.bsl"}, 3), (None, None))

    def test_plan_version_note(self):
        self.assertIsNone(report.plan_version_note({"schema": model.PLAN_SCHEMA}))
        self.assertIn("формата 2", report.plan_version_note({"version": 2}))

    def test_uncovered_by_needs(self):
        """«не покрыто этим прогоном» — по средству, которое закроет."""
        def u(owner, ctx, needs):
            return {"executor": "plan", "kind": "uncovered", "module": "m.bsl", "owner": owner, "context": ctx,
                    "needs": needs, "outcome": "skipped", "message": "причина", "line": None}
        run = {"results": [u("ОбщийМодуль.А", "WebClient", "designer:WebClient"),
                           u("ОбщаяКоманда.К", "*", "ui"), u("HTTPСервис.С", "*", "publication")], "notes": []}
        s = report.build(PLAN, {"findings": []}, run, {})
        self.assertEqual([i["needs"] for i in s["uncovered"]], ["designer:WebClient", "ui", "publication"])
        md = report.markdown(s)
        self.assertIn("## Не покрыто этим прогоном (3)\n### Полная проверка Конфигуратором — закроет "
                      "`otk gate --designer` или `otk designer-check`", md)
        self.assertIn("- ОбщийМодуль.А [веб-клиент] — причина", md)
        self.assertIn("### UI и сценарные тесты", md)
        self.assertIn("### Проверка с публикацией", md)
        self.assertNotIn("uncovered", [i["what"].split()[0] for i in s["notChecked"]])

    def test_nothing_checked(self):
        s = report.build(PLAN, None, {"results": [], "notes": []}, {})
        self.assertEqual((s["code"], s["verdict"]), (2, "ничего не проверено"))

    def test_nothing_to_check_and_infra(self):
        empty = {**PLAN, "modules": [], "queries": []}
        self.assertEqual(report.build(empty, None, None, {})["code"], 0)
        self.assertEqual(report.build(PLAN, {"findings": []}, None, {}, ["база занята"])["code"], 2)

    def test_query_downgrades(self):
        m = "CommonModules/М/Ext/Module.bsl"
        plan = {**PLAN, "queries": [{"id": "q1", "module": m, "line": 5, "complete": True, "text": "…"},
                                    {"id": "q2", "module": m, "line": 9, "complete": True, "unchanged": True,
                                     "text": "…"},
                                    {"id": "q3", "module": m, "line": 12, "complete": True, "assembled": True,
                                     "text": "…"}]}
        run = {"results": [{"executor": "com", "kind": "query", "module": m, "owner": q, "context": "Server",
                            "outcome": "compile-error", "line": 1, "message": "Поле не найдено", "query": q}
                           for q in ("q1", "q2", "q3")], "notes": []}
        sev = [f["severity"] for f in report.build(plan, {"findings": []}, run, {})["findings"]]
        self.assertEqual(sev, ["error", "warning", "warning"])
        # метаданные менялись — неизменный запрос мог сломаться правкой: ошибка
        plan["metadata"] = [{"path": "Catalogs/К.xml", "status": "M"}]
        sev = [f["severity"] for f in report.build(plan, {"findings": []}, run, {})["findings"]]
        self.assertEqual(sev, ["error", "error", "warning"])

    def test_foreign_temp_table_not_checked(self):
        from otk_1c.probes.executors import query_result
        q = {"id": "q1", "module": "m.bsl", "line": 10}
        r = query_result("com", q, "Server", '{(3, 2)}: Таблица не найдена "ДанныеУпаковок" <<?>>ДанныеУпаковок.*')
        self.assertEqual((r["outcome"], r["line"]), ("skipped", 12))
        r = query_result("com", q, "Server", '{(1, 9)}: Таблица не найдена "Справочник.Х"')
        self.assertEqual(r["outcome"], "compile-error")

    def test_unknown_columns_not_checked(self):
        from otk_1c.probes.executors import query_result
        text = ("SELECT Code, Description INTO Classifier FROM &Classifier AS Classifier;\n"
                "SELECT C.Ref FROM Catalog.WorldCountries AS C INNER JOIN Classifier AS Classifier "
                "ON C.Code = Classifier.Code")
        q = {"id": "q1", "module": "m.bsl", "line": 10, "text": text}
        self.assertEqual(query_result("com", q, "Server", '{(1, 8)}: Поле не найдено "Code"')["outcome"], "skipped")
        self.assertEqual(query_result("com", q, "Server", '{(2, 8)}: Поле не найдено "Classifier.Codee"')["outcome"],
                         "skipped")
        # поле таблицы метаданных — ошибка, даже если в пакете есть параметр-таблица
        self.assertEqual(query_result("com", q, "Server", '{(2, 8)}: Поле не найдено "C.Reff"')["outcome"],
                         "compile-error")
        q["text"] = "ВЫБРАТЬ Т.Ссылка ИЗ Справочник.Х КАК Т"          # источников с неизвестными колонками нет
        self.assertEqual(query_result("com", q, "Server", '{(1, 8)}: Поле не найдено "Т.Ссылкаа"')["outcome"],
                         "compile-error")

    def test_markdown_groups_warnings(self):
        static = {"findings": [{"check": "bslls:MagicNumber", "severity": "warning", "module": "m.bsl", "line": i,
                                "message": "магическое число"} for i in range(1, 6)]}
        md = report.markdown(report.build(PLAN, static, None, {}))
        self.assertIn("**bslls:MagicNumber**](", md)
        self.assertIn("#bslls) ×5", md)

    def test_module_skip_visible(self):
        """Пропуск по модулю с проверкой реестра — в JUnit и Problems с путём модуля; без check — только
        в сводке."""
        results = [{"executor": "com", "kind": "session", "module": "ext.bsl", "owner": None,
                    "context": "ExternalConnection", "outcome": "skipped", "message": "COM недоступен",
                    "line": None, "check": "probes-server"},
                   {"executor": "com", "kind": "probe", "module": "m.bsl", "owner": None, "context": "Server",
                    "outcome": "skipped", "message": "недоступно через COM", "line": None}]
        items = report.not_checked({"source": "src", "queries": []}, {"results": results})
        summary = {"findings": [], "notChecked": items}
        junit = report.junit(summary)
        self.assertIn('skipped="1"', junit)
        self.assertIn('name="probes-server src/ext.bsl"', junit)
        problems = report.problems(summary, "repo")
        self.assertEqual(len(problems), 1)
        self.assertIn("ext.bsl(1,1): warning not-checked/probes-server: session [com/ExternalConnection]",
                      problems[0])

    def test_sarif(self):
        """SARIF 2.1.0 — правило на код, путь от корня репозитория, отпечаток без строки, «не проверено» —
        kind open без уровня."""
        static = {"findings": [{"check": "metadata-ref", "severity": "error",
                                "module": "CommonModules/М/Ext/Module.bsl",
                                "line": 7, "message": "Объект не найден: Справочники.Х"},
                               {"check": "metadata-ref", "severity": "warning",
                                "module": "CommonModules/М/Ext/Module.bsl",
                                "line": 8, "message": "Неоднозначно"}]}
        s = report.build(PLAN, static, None, {"collect": 1})
        s["notChecked"].append({"what": "session [com/Server]", "reason": "COM недоступен", "check": "probes-server",
                                "file": "src/cf/x.bsl"})
        run = report.sarif(s, "D:/r")["runs"][0]
        self.assertEqual([r["id"] for r in run["tool"]["driver"]["rules"]], ["metadata-ref"])
        err, warn, skipped = run["results"]
        self.assertEqual((err["level"], warn["level"], err["ruleIndex"]), ("error", "warning", 0))
        loc = err["locations"][0]["physicalLocation"]
        self.assertEqual(loc["artifactLocation"]["uriBaseId"], "SRCROOT")
        self.assertIn("%D0%9C", loc["artifactLocation"]["uri"])          # «М» — в процентной кодировке
        self.assertTrue(loc["artifactLocation"]["uri"].endswith("/Ext/Module.bsl"))
        self.assertEqual(loc["region"]["startLine"], 7)
        self.assertTrue(run["originalUriBaseIds"]["SRCROOT"]["uri"].startswith("file:///"))
        self.assertEqual((skipped["kind"], skipped["level"], skipped["ruleId"]),
                         ("open", "none", "not-checked/probes-server"))
        moved = report.build(PLAN, {"findings": [{**static["findings"][0], "line": 30}]}, None, {})
        self.assertEqual(report.sarif(moved, "D:/r")["runs"][0]["results"][0]["partialFingerprints"],
                         err["partialFingerprints"])


class TestDefectCommit(unittest.TestCase):
    def test_commit_outside_branches(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-ctl-"))
        try:
            def git(*a):
                r = subprocess.run(["git", "-C", str(tmp), *a], check=True, capture_output=True)
                return r.stdout.decode().strip()
            git("init", "-q", "-b", "master")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            (tmp / "a.bsl").write_text("старый", encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            head = git("rev-parse", "HEAD")
            sha = controls.make_commit(tmp, head, {"a.bsl": "новый", "b.bsl": "добавлен"}, "дефект")
            self.assertEqual(git("rev-parse", "HEAD"), head)                       # ветка не сдвинулась
            self.assertEqual((tmp / "a.bsl").read_text(encoding="utf-8"), "старый")  # рабочее дерево не тронуто
            self.assertEqual(git("status", "--porcelain"), "")                    # индекс не тронут
            self.assertEqual(git("show", f"{sha}:b.bsl").lstrip("\ufeff"), "добавлен")
            self.assertEqual(git("rev-parse", f"{sha}~1"), head)
        finally:
            rmtree_force(tmp)


class FindingsDocTest(unittest.TestCase):
    """Каждый код находки описан в docs/findings.md, ссылки сводки ведут в существующий раздел."""
    ROOT = Path(__file__).resolve().parent.parent

    def sections(self):
        import re
        text = (self.ROOT / "docs" / "findings.md").read_text(encoding="utf-8")
        return {m.group(1) for m in re.finditer(r"^#{2,3} (?:[^\n]*?)?([a-z][a-z-]*)$", text, re.M)}, text

    def test_every_code_documented(self):
        import re

        from otk_1c import checks, model
        sections, text = self.sections()
        src = self.ROOT / "src" / "otk_1c" / "gate"
        static_codes = set()
        for name in ("static.py", "platform_api.py"):
            static_codes |= set(re.findall(r'emit\(\s*"([a-z-]+)"', (src / name).read_text(encoding="utf-8")))
        static_codes |= set(re.findall(r'"check": "([a-z-]+)"', (src / "static.py").read_text(encoding="utf-8")))
        self.assertTrue({"metadata-ref", "platform-api", "dependent", "handler"} <= static_codes)
        kinds = {"probe", "query", "session", "extcheck", "load", "scenario", "smoke", "designer"}
        for code in static_codes | kinds | {"bslls", "not-checked", "uncovered"}:
            self.assertIn(code, sections, f"нет раздела {code} в docs/findings.md")
        for c in checks.CHECKS:
            self.assertIn(f"`not-checked/{c.name}`", text)
        for o in model.Outcome:
            if o not in (model.Outcome.OK, model.Outcome.SKIPPED):
                self.assertIn(f"`{o.value}`", text)
        for group in report.NEEDS_TITLES:
            self.assertIn(f"`{group}", text)
        from otk_1c.analyzers import bsl_ls
        for code in bsl_ls.FAIL_CODES:
            self.assertIn(f"`bslls:{code}`", text)

    def test_links(self):
        sections, _ = self.sections()
        for code in ("metadata-ref", "bslls:ParseError", "probe/compile-error", "designer/compile-error",
                     "not-checked/analyzers", "load/compile-error"):
            self.assertIn(report.doc_anchor(code), sections)
        finding = {"check": "metadata-ref", "severity": "error", "module": "CommonModules/М/Ext/Module.bsl", "line": 2,
                   "message": "нет"}
        s = report.build(PLAN, {"findings": [finding]}, None, {})
        self.assertIn(f"[`metadata-ref`]({report.FINDINGS_DOC}#metadata-ref)", report.markdown(s))
        self.assertIn("справка: ", report.junit(s))


class DesignerStepTest(unittest.TestCase):
    """Шаг designer в сводке gate — находки с источниками, снятие designer:*, один код выхода."""
    MOD = "CommonModules/М/Ext/Module.bsl"
    PLAN = {**PLAN, "queries": [], "modules": [{"path": MOD, "kind": "CommonModule", "owner": "ОбщийМодуль.М"}]}

    @staticmethod
    def designer(*findings, ran=True, modes=("-Server", "-ThinClient"), **kw):
        return {"ran": ran, "modes": list(modes), "control": {"ok": True, "detail": "ок"}, "rc": 1 if findings else 0,
                "findings": list(findings), "known": 3, "fixed": 1, "baseline": "b.json", "notes": [], "infra": [],
                "timings": {}, **kw}

    @staticmethod
    def dfind(obj, file, line, msg="Переменная не определена (Х)", modes=("Сервер",)):
        return {"object": obj, "file": file, "line": line, "col": 5, "message": msg, "code": "", "modes": list(modes)}

    def uncov(self, ctx, needs):
        return {"executor": "plan", "kind": "uncovered", "module": self.MOD, "owner": "ОбщийМодуль.М",
                "context": ctx, "needs": needs, "outcome": "skipped", "message": "причина", "line": None}

    def test_same_line_is_source(self):
        static = {"findings": [{"check": "metadata-ref", "severity": "error", "module": self.MOD, "line": 2,
                                "message": "нет такого"}]}
        d = self.designer(self.dfind("ОбщийМодуль.М.Модуль", f"src/cf/{self.MOD}", 2),
                          self.dfind("ОбщийМодуль.Другой.Модуль", "src/cf/CommonModules/Другой/Ext/Module.bsl", 7,
                                     modes=("Сервер", "Тонкий клиент")))
        texts = {"CommonModules/Другой/Ext/Module.bsl": "\n" * 5 + "Процедура П()\n\tХ();\nКонецПроцедуры\n"}
        s = report.build(self.PLAN, static, {"results": [], "notes": []}, {}, read=texts.get, designer=d)
        self.assertEqual(s["code"], 1)
        self.assertEqual(len(s["findings"]), 2)
        first, other = s["findings"]
        self.assertEqual([x["step"] for x in first["sources"]], ["static", "designer"])
        self.assertEqual(other["step"], "designer")
        self.assertEqual((other["object"], other["place"], other.get("method"), other.get("text")),
                         ("ОбщийМодуль.Другой", "ОбщийМодуль.Другой.Модуль(7)", "П", "Х();"))
        self.assertIn("полная проверка Конфигуратором (сервер, тонкий клиент)", report.markdown(s))
        self.assertEqual(s["designer"]["new"], 2)

    def test_without_file_and_load_error(self):
        d = self.designer(self.dfind("Конфигурация.МодульСеанса", None, 7),
                          {"object": self.MOD, "file": f"src/cf/{self.MOD}", "line": 1, "col": 1, "message": "битый",
                           "code": "", "modes": [], "load": True})
        s = report.build(self.PLAN, {"findings": []}, {"results": [], "notes": []}, {}, designer=d)
        codes = sorted(f["rule"] for f in s["findings"])
        self.assertEqual(codes, ["designer/compile-error", "load/compile-error"])
        f = next(f for f in s["findings"] if f["file"] is None)
        self.assertEqual((f["object"], f["place"]), ("Конфигурация", "Конфигурация.МодульСеанса(7)"))
        report.junit(s), report.problems(s, "repo"), report.markdown(s)

    def test_closes_uncovered_in_its_modes(self):
        run = {"results": [self.uncov("Server", "designer:Server"), self.uncov("WebClient", "designer:WebClient"),
                           self.uncov("*", "designer:*"), self.uncov("*", "ui")], "notes": [],
               "notChecked": [{"what": "проба", "reason": "сбой", "needs": "designer:ThinClient"}]}
        s = report.build(self.PLAN, {"findings": []}, run, {}, designer=self.designer())
        self.assertEqual([i["needs"] for i in s["uncovered"]], ["designer:WebClient", "ui"])
        self.assertEqual([i.get("needs") for i in s["notChecked"]], [])
        self.assertEqual((s["code"], s["designer"]["closed"]), (0, 3))
        self.assertIn("снято с «не покрыто» и «не проверено»: 3", report.markdown(s))
        # не выполнена целиком — ничего не снимается
        s2 = report.build(self.PLAN, {"findings": []}, run, {}, designer=self.designer(ran=False))
        self.assertEqual(len(s2["uncovered"]), 4)
        self.assertIn("Проверка Конфигуратором: не выполнена", report.markdown(s2))

    def test_infra_is_code_2(self):
        d = self.designer(ran=False, infra=["проверка Конфигуратором не работает: x"])
        s = report.build(self.PLAN, {"findings": []}, {"results": [], "notes": []}, {}, designer=d)
        self.assertEqual(s["code"], 2)
        self.assertIn("проверка Конфигуратором не работает: x", s["notes"])

    def test_counts_as_checked(self):
        s = report.build(self.PLAN, None, {"results": [], "notes": []}, {}, designer=self.designer())
        self.assertEqual(s["code"], 0)
        self.assertNotIn("designer", report.build(self.PLAN, None, None, {}))

    def test_schema(self):
        from schema_check import errors, load
        d = self.designer(self.dfind("ОбщийМодуль.М.Модуль", f"src/cf/{self.MOD}", 2))
        s = report.build(self.PLAN, {"findings": []}, {"results": [], "notes": []}, {}, designer=d)
        self.assertEqual(errors(s, load("summary")), [])


class TestRepeated(unittest.TestCase):
    """Частые причины и краткая сводка в консоль на большой разнице."""
    MOD = "CommonModules/М{}/Ext/Module.bsl"

    def summary(self):
        mk = lambda i, msg: {"check": "metadata-ref", "severity": "error", "module": self.MOD.format(i % 2),  # noqa: E731
                             "line": i, "message": msg}
        findings = [mk(i, "нет объекта <Справочник.Х>" + (" (похоже: Справочник.Ч)" if i % 2 else ""))
                    for i in range(1, 5)] + [mk(10, "нет объекта <Справочник.Ю>")]
        plan = {**PLAN, "queries": [], "modules": [{"path": self.MOD.format(0)}, {"path": self.MOD.format(1)}]}
        return report.build(plan, {"findings": findings}, None, {"static": 1})

    def test_repeated(self):
        """Одна причина в 4 местах двух модулей (подсказка «похоже» не мешает) — одна строка; одиночная — нет."""
        s = self.summary()
        self.assertEqual(report.repeated(s["findings"]),
                         [("metadata-ref", "нет объекта <Справочник.Х>", 4, 2)])
        md = report.markdown(s)
        self.assertIn("Частые причины (1):", md)
        self.assertIn("нет объекта <Справочник.Х> — мест: 4, модулей: 2", md)

    def test_console_brief(self):
        """Находок больше порога — в консоль краткая сводка; --full — полный summary.md."""
        s = self.summary()
        self.assertEqual(report.console(s), report.markdown(s))
        old, report.BRIEF_OVER = report.BRIEF_OVER, 3
        try:
            text = report.console(s)
            self.assertIn("`metadata-ref` 5", text)
            self.assertIn("Больше всего ошибок (объектов с ошибками: 2):", text)
            self.assertIn("в консоль целиком — --full", text)
            self.assertNotIn("### ", text)
            self.assertEqual(report.console(s, full=True), report.markdown(s))
        finally:
            report.BRIEF_OVER = old


def full_summary():
    """Сводка со всеми разделами: ошибки со строкой кода, предупреждения, не проверено, не покрыто, примечания."""
    mod = "CommonModules/М/Ext/Module.bsl"
    plan = {**PLAN, "queries": [], "notes": ["справка 8.5.4.1878, платформа проверки 8.5.6: <примечание>"],
            "modules": [{"path": mod, "kind": "CommonModule", "owner": "ОбщийМодуль.М"}]}
    static = {"findings": [{"check": "metadata-ref", "severity": "error", "module": mod, "line": 2,
                            "message": "нет объекта <Справочник.Х>"}]
              + [{"check": "bslls:MagicNumber", "severity": "warning", "module": mod, "line": i,
                  "message": "магическое число"} for i in range(1, 6)],
              "notChecked": [{"what": "запрос q1", "reason": "текст собирается в коде", "check": "queries",
                              "file": "src/cf/" + mod, "line": 5, "needs": "ui"}]}
    run = {"results": [{"executor": "plan", "kind": "uncovered", "module": mod, "owner": "ОбщийМодуль.М",
                        "context": "WebClient", "needs": "designer:WebClient", "outcome": "skipped",
                        "message": "причина", "line": None}], "notes": []}
    texts = {mod: "Процедура А() Экспорт\n\tСправочники.Х.Создать();\nКонецПроцедуры\n"}
    return report.build(plan, static, run, {"static": 1.5}, read=texts.get)


class TestQueryNeeds(unittest.TestCase):
    """Пропуск запроса Конфигуратором не закрывается (текст запроса он не разбирает)."""

    def test_query_skips(self):
        def r(kind, msg, ctx="Server"):
            return {"executor": "com", "kind": kind, "module": "m.bsl", "owner": "q1", "context": ctx,
                    "outcome": "skipped", "message": msg, "line": 3}
        run = {"results": [r("query", "временная таблица другого запроса «ВТ» — без неё не разобрать"),
                           r("query", "не выполнено: COM-соединение не установлено"),
                           r("probe", "не выполнено: сеанс YAxUnit не стартовал", "ThinClient"),
                           r("probe", "контекст недоступен", "ThinClient")], "notes": []}
        s = report.build({**PLAN, "queries": []}, {"findings": []}, run, {})
        self.assertEqual([i.get("needs") for i in s["notChecked"]], ["ui", None, "designer:ThinClient",
                                                                 "designer:ThinClient"])
        self.assertNotIn("Конфигуратором (сервер)", report.markdown(s))


class TestRequiredContexts(unittest.TestCase):
    """environment.requiredContexts: непокрытый обязательный контекст — код 2 и вердикт; закрытый полной
    проверкой Конфигуратором — не влияет."""
    MOD = "CommonModules/М/Ext/Module.bsl"

    def plan(self, **kw):
        return {**PLAN, "queries": [], "modules": [
            {"path": self.MOD, "kind": "CommonModule", "owner": "ОбщийМодуль.М",
             "contexts": ["Server", "OrdinaryClient"], **kw}]}

    @staticmethod
    def unc(ctx="OrdinaryClient", needs="designer:OrdinaryClient"):
        return {"executor": "plan", "kind": "uncovered", "module": TestRequiredContexts.MOD,
                "owner": "ОбщийМодуль.М", "context": ctx, "needs": needs, "outcome": "skipped",
                "message": "контекст недоступен локально", "line": None}

    def test_missing_required_is_code_2(self):
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        s = report.build(plan, {"findings": []}, {"results": [self.unc()], "notes": []}, {})
        self.assertEqual((s["code"], s["verdict"]), (2, "не покрыто обязательное: OrdinaryClient"))
        self.assertEqual([i["context"] for i in s["uncovered"]], ["OrdinaryClient"])   # запись остаётся

    def test_closed_by_designer(self):
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        designer = {"ran": True, "modes": ["-ThickClientOrdinaryApplication"], "findings": [], "notes": []}
        s = report.build(plan, {"findings": []}, {"results": [self.unc()], "notes": []}, {}, designer=designer)
        self.assertEqual((s["code"], s["verdict"]), (0, "можно отправлять"))
        self.assertEqual(s["uncovered"], [])

    def test_wildcard_of_its_own_module(self):
        """Запись «не покрыто» с context "*" относится к своему модулю (по пути), а не ко всем модулям того же
        владельца: иначе неизвестный файл дал бы ложный код 2 по чужому контексту."""
        other = "CommonModules/Н/Ext/Module.bsl"
        plan = {**PLAN, "queries": [], "requiredContexts": ["Server"], "modules": [
            {"path": self.MOD, "kind": "CommonModule", "owner": "ОбщийМодуль.М", "contexts": ["Server"]},
            {"path": other, "kind": "Unknown", "owner": "ОбщийМодуль.М", "contexts": ["OrdinaryClient"]}]}
        run = {"results": [{"executor": "plan", "kind": "uncovered", "module": other, "owner": "ОбщийМодуль.М",
                            "context": "*", "needs": "designer:*", "outcome": "skipped",
                            "message": "неизвестный вид модуля", "line": None},
                           {"executor": "com", "kind": "probe", "module": self.MOD, "owner": "ОбщийМодуль.М",
                            "context": "Server", "outcome": "ok", "message": "", "line": None}], "notes": []}
        s = report.build(plan, {"findings": []}, run, {})
        self.assertEqual(s["code"], 0)
        self.assertEqual([(i["context"], i["file"]) for i in s["uncovered"]],
                         [("*", "src/cf/" + other)])

    def test_unknown_flags_block_requirement(self):
        """Заимствованный общий модуль без --parent: контексты неизвестны, и требование подтвердить нечем, даже
        если остальные модули проверены, — этот модуль мог быть в требуемом контексте."""
        dep = "CommonModules/З/Ext/Module.bsl"
        plan = {**PLAN, "queries": [], "requiredContexts": ["Server"], "modules": [
            {"path": self.MOD, "kind": "CommonModule", "owner": "ОбщийМодуль.М", "contexts": ["Server"]},
            {"path": dep, "kind": "CommonModule", "owner": "ОбщийМодуль.З", "contexts": [],
             "probe": {"strategy": "none", "reason": "заимствованный общий модуль: флаги — в основной конфигурации"}}]}
        run = {"results": [{"executor": "com", "kind": "probe", "module": self.MOD, "owner": "ОбщийМодуль.М",
                            "context": "Server", "outcome": "ok", "message": "", "line": None}], "notes": []}
        s = report.build(plan, {"findings": []}, run, {})
        self.assertEqual((s["code"], s["verdict"]), (2, "не покрыто обязательное: Server"))

    def test_unknown_flags_closed_by_designer(self):
        """Полная проверка Конфигуратором в режиме контекста проверяет и модуль с неизвестными контекстами:
        требование закрыто, код 2 за него не выставляется."""
        dep = "CommonModules/З/Ext/Module.bsl"
        plan = {**PLAN, "queries": [], "requiredContexts": ["OrdinaryClient"], "modules": [
            {"path": self.MOD, "kind": "CommonModule", "owner": "ОбщийМодуль.М", "contexts": ["OrdinaryClient"]},
            {"path": dep, "kind": "CommonModule", "owner": "ОбщийМодуль.З", "contexts": [],
             "probe": {"strategy": "none", "reason": "заимствованный общий модуль: флаги — в основной конфигурации"}}]}
        designer = {"ran": True, "modes": ["-ThickClientOrdinaryApplication"], "findings": [], "notes": []}
        s = report.build(plan, {"findings": []}, {"results": [self.unc(needs="designer:*")], "notes": []}, {},
                         designer=designer)
        self.assertEqual((s["code"], s["verdict"]), (0, "можно отправлять"))
        self.assertEqual(s["uncovered"], [])

    def test_module_without_probe_counts(self):
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        s = report.build(plan, {"findings": []},
                         {"results": [self.unc("*", "designer:*")], "notes": []}, {})
        self.assertEqual(s["code"], 2)

    def test_wildcard_needs_its_mode(self):
        """Запись «не покрыто» модуля без пробы (needs designer:*) закрывается сокращённым набором режимов только в
        проверенных контекстах: обязательный OrdinaryClient без своего режима остаётся непокрытым."""
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        run = {"results": [self.unc("*", "designer:*")], "notes": []}
        designer = {"ran": True, "modes": ["-Server"], "findings": [], "notes": []}
        s = report.build(plan, {"findings": []}, run, {}, designer=designer)
        self.assertEqual((s["code"], s["verdict"]), (2, "не покрыто обязательное: OrdinaryClient"))
        self.assertEqual([i["context"] for i in s["uncovered"]], ["*"])      # запись остаётся: не проверено целиком
        designer = {**designer, "modes": ["-Server", "-ThickClientOrdinaryApplication"]}
        s = report.build(plan, {"findings": []}, run, {}, designer=designer)
        self.assertEqual((s["code"], s["verdict"]), (0, "можно отправлять"))
        self.assertEqual(s["uncovered"], [])

    def test_record_about_covered_module_does_not_block(self):
        """Запись «не покрыто» не мешает, если модуль проверен другой выполненной проверкой в этом контексте:
        записи — то, что осталось не закрытым, а не приговор."""
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        entry = {**self.unc(), "file": "src/cf/" + self.MOD}
        run = {"results": [entry], "notes": [],
               "extCheck": {"modules": [self.MOD], "contexts": ["OrdinaryClient"]}}
        s = report.build(plan, {"findings": []}, run, {})
        self.assertEqual((s["code"], s["verdict"]), (0, "можно отправлять"))

    def test_query_result_does_not_cover(self):
        """Результат запроса проверяет текст запроса, а не модуль: покрытием контекста он не считается."""
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        run = {"results": [{"executor": "com", "kind": "query", "module": self.MOD, "owner": "q1",
                            "context": "OrdinaryClient", "outcome": "ok", "message": "", "line": 1}],
               "notes": []}
        self.assertEqual(report.build(plan, {"findings": []}, run, {})["code"], 2)

    def test_unknown_required_context_value(self):
        """Неизвестное значение или не список (в том числе null) — требование выполнено быть не может: ошибка
        конфигурации; проверка покрытия тогда не выполняется и сводка не падает."""
        for value in ("Server", ["Ordinaryclient"], [""], [None], 1, None):
            plan = {**self.plan(), "requiredContexts": value}
            result = report.build(plan, {"findings": []}, {"results": [], "notes": []}, {})
            self.assertEqual(result["code"], 2, value)
            self.assertEqual([i["reasonCode"] for i in result["infra"]], ["config-invalid"], value)

    def test_invalid_contexts_available(self):
        """contextsAvailable — только проверяемые контексты (без WebClient): иначе план не проходит схему."""
        for value in (["WebClient"], ["Server", "Ordinaryclient"], "Server"):
            plan = {**self.plan(), "requiredContexts": ["Server"], "contextsAvailable": value}
            result = report.build(plan, {"findings": []}, {"results": [], "notes": []}, {})
            self.assertEqual(result["code"], 2, value)
            self.assertEqual([i["reasonCode"] for i in result["infra"]], ["config-invalid"], value)

    def test_no_module_in_context_is_ok(self):
        plan = {**self.plan(contexts=["Server"]), "requiredContexts": ["OrdinaryClient"]}
        s = report.build(plan, {"findings": []}, {"results": [], "notes": []}, {})
        self.assertEqual(s["code"], 0)

    def test_covered_by_done_check_not_by_silence(self):
        """Покрытие — по выполненной проверке модуля, а не по отсутствию записей: расширенная проверка закрывает
        требование и без записей, а --static-only (пробы не выполнялись, записей нет) — не закрывает."""
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        run = {"results": [], "extCheck": {"modules": [self.MOD], "contexts": ["Server", "OrdinaryClient"]},
               "notes": []}
        s = report.build(plan, {"findings": []}, run, {})
        self.assertEqual((s["code"], s["verdict"]), (0, "можно отправлять"))
        s = report.build(plan, {"findings": []}, {"results": [], "notes": []}, {})
        self.assertEqual((s["code"], s["verdict"]), (2, "не покрыто обязательное: OrdinaryClient"))

    def test_probe_result_covers_context(self):
        plan = {**self.plan(), "requiredContexts": ["Server"]}
        run = {"results": [{"executor": "com", "kind": "probe", "module": self.MOD, "owner": "ОбщийМодуль.М",
                            "context": "Server", "outcome": "ok", "message": "", "line": None}], "notes": []}
        self.assertEqual(report.build(plan, {"findings": []}, run, {})["code"], 0)
        run["results"][0]["outcome"] = "skipped"           # пропуск пробы — не проверка
        self.assertEqual(report.build(plan, {"findings": []}, run, {})["code"], 2)

    def test_skipped_probe_by_context(self):
        plan = {**self.plan(), "requiredContexts": ["OrdinaryClient"]}
        run = {"results": [{"executor": "com", "kind": "probe", "module": self.MOD, "owner": "ОбщийМодуль.М",
                            "context": "OrdinaryClient", "outcome": "skipped", "message": "не выполнено: COM",
                            "line": None}], "notes": []}
        s = report.build(plan, {"findings": []}, run, {})
        self.assertEqual([i.get("needs") for i in s["notChecked"]], ["designer:OrdinaryClient"])
        self.assertEqual(s["code"], 2)


class TestHtml(unittest.TestCase):
    """Summary.html — один файл, все разделы, экранирование, открытие."""

    def test_sections_and_escape(self):
        s = full_summary()
        h = report_html.html(s)
        for kind in ("errors", "warnings", "notchecked", "uncovered"):
            self.assertIn(f'data-kind="{kind}"', h)
            self.assertIn(f'id="f-{kind}"', h)
        self.assertIn("Ошибки (1)", h)
        self.assertIn("Предупреждения (5)", h)
        self.assertIn("<pre>Справочники.Х.Создать();</pre>", h)                # строка кода
        self.assertIn(f'href="{report.FINDINGS_DOC}#metadata-ref"', h)
        self.assertIn("&lt;Справочник.Х&gt;", h)                               # экранирование
        self.assertNotIn("<Справочник.Х>", h)
        self.assertIn("&lt;примечание&gt;", h)
        self.assertNotRegex(h, r'<(script|link)[^>]+(src|href)=')               # без внешних зависимостей
        self.assertIn("ОбщийМодуль.М [веб-клиент]", h)
        self.assertIn("<code>otk gate --designer</code>", h)                # `код` из подсказок

    def test_empty_summary(self):
        s = report.build({**PLAN, "queries": []}, {"findings": []}, None, {})
        self.assertEqual(report_html.html(s).count("<p>нет</p>"), 4)

    def test_write_and_open(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            output.write(full_summary(), tmp, "repo")
            path = Path(tmp) / "summary.html"
            self.assertTrue(path.exists())
            with mock.patch.dict("os.environ", {}, clear=False) as env, \
                    mock.patch.object(report_html.webbrowser, "open") as browser:
                env.pop("CI", None)
                report_html.open_summary(tmp)
                browser.assert_called_once_with(path.resolve().as_uri())
                env["CI"] = "true"                                              # в CI не открывается
                report_html.open_summary(tmp)
                browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()
