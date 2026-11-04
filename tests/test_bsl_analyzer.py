"""bsl-analyzer до и после и адаптер анализаторов: без анализатора — подмена _analyze."""
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import analyzers, checks
from otk_1c.analyzers import bsl_analyzer, bsl_ls
from otk_1c.sources import layout

MOD = "CommonModules/Общий/Ext/Module.bsl"
CALL = "Неизвестный.Метод();"


def fake_analyze(exe, root, rels, cfg_path, cache, out, extension=None, parent=None, timeout=None):
    """Находка на каждую строку с вызовом Неизвестный.Метод(); SelfAssign — на «а = а;». Читает файлы дерева root —
    так видно, что зеркало получило базовую редакцию."""
    found = {}
    for rel in rels:
        for no, line in enumerate((Path(root) / rel).read_text(encoding="utf-8-sig").splitlines()):
            if CALL in line:
                found.setdefault(rel, []).append({"code": "UnresolvedMethodCall", "line": no,
                                                  "message": "Метод 'Метод' не найден у 'Неизвестный'"})
            if "а = а;" in line:
                found.setdefault(rel, []).append({"code": "SelfAssign", "line": no, "message": "Присваивание"})
            if "ЭтотОбъект = " in line:
                found.setdefault(rel, []).append({"code": "ThisObjectAssign", "line": no,
                                                  "message": "Свойство ЭтотОбъект доступно только для чтения"})
    return found, None


class TomlTest(unittest.TestCase):
    def test_dump_round_trip(self):
        params = {"ParseError": True, "Line Length": False, "CyclomaticComplexity": {"n": 20}}
        data = {"min_platform_version": "8.3.21", "diagnostics": {"parameters": params}, "list": ["а", 'b"c']}
        self.assertEqual(tomllib.loads(bsl_analyzer.dump_toml(data)), data)


class NativeConfigTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, self.repo)
        self.rules = ["ParseError", "LineLength", "UnresolvedMethodCall", bsl_analyzer.COMPAT_RULE]

    def test_builtin(self):
        data, sources = bsl_analyzer.native_config(self.repo, {}, self.rules)
        params = data["diagnostics"]["parameters"]
        self.assertEqual(params, {"ParseError": True, "LineLength": False, "UnresolvedMethodCall": True,
                                  bsl_analyzer.COMPAT_RULE: True})
        self.assertIn("встроенный", sources["основа"])

    def test_legacy_json_only(self):
        (self.repo / ".bsl-language-server.json").write_text(json.dumps(
            {"diagnostics": {"mode": "only", "parameters": {"LineLength": {"maxLineLength": 150}}}}), encoding="utf-8")
        params = bsl_analyzer.native_config(self.repo, {}, self.rules)[0]["diagnostics"]["parameters"]
        self.assertEqual(params["LineLength"], {"maxLineLength": 150})
        self.assertFalse(params["ParseError"])

    def test_own_toml_with_overlay(self):
        (self.repo / "bsl-analyzer.toml").write_text(
            '[source]\nroot = "src/cf"\n[diagnostics.parameters]\nCommentedCode = false\n', encoding="utf-8")
        data, sources = bsl_analyzer.native_config(self.repo, {"minPlatform": "8.3.24", "compatWarnings": False},
                                                   self.rules)
        self.assertNotIn("source", data)            # состав исходников задаёт адаптер
        self.assertEqual(data["min_platform_version"], "8.3.24")
        self.assertEqual(data["diagnostics"]["parameters"], {"CommentedCode": False, bsl_analyzer.COMPAT_RULE: False})
        self.assertEqual(sources["основа"], "bsl-analyzer.toml")
        self.assertIn("minPlatform", sources["min_platform_version"])
        self.assertIn("compatWarnings", sources[bsl_analyzer.COMPAT_RULE])


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, self.tmp)
        self.root = self.tmp / "cf"
        for rel in (MOD, "CommonModules/Другой/Ext/Module.bsl", "Catalogs/Товары/Ext/ObjectModule.bsl"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("исходник", encoding="utf-8")
        (self.root / "Configuration.xml").write_text("<x/>", encoding="utf-8")
        (self.root / "CommonModules" / "Общий.xml").write_text("<m/>", encoding="utf-8")

    def test_mirror_keeps_sources(self):
        m = bsl_analyzer.mirror(self.root, self.tmp / "m", {("CommonModules", "Общий")})
        self.assertEqual((m / "Catalogs/Товары/Ext/ObjectModule.bsl").read_text(encoding="utf-8"), "исходник")
        bsl_analyzer._write(m / MOD, "до")
        (m / "CommonModules/Общий/Ext/Module.bsl").unlink()
        bsl_analyzer._write(m / "Configuration.xml", "<другое/>")        # жёсткая ссылка: исходник не меняется
        bsl_analyzer.remove_mirror(m)
        self.assertFalse(m.exists())
        for rel in (MOD, "CommonModules/Другой/Ext/Module.bsl", "Catalogs/Товары/Ext/ObjectModule.bsl"):
            self.assertEqual((self.root / rel).read_text(encoding="utf-8"), "исходник")
        self.assertEqual((self.root / "Configuration.xml").read_text(encoding="utf-8"), "<x/>")

    def test_protected_modules(self):
        d = self.root / "CommonModules" / "Закрытый" / "Ext"
        d.mkdir(parents=True)
        (d / "Module.bin").write_bytes(b"\0")
        self.assertEqual(bsl_analyzer.protected_modules(self.root), {"закрытый"})


class SarifTest(unittest.TestCase):
    def test_parse(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, tmp)
        root = tmp / "cf"
        uri = "//?/" + (root / MOD).resolve().as_posix()
        data = {"runs": [{"results": [
            {"ruleId": "ParseError", "message": {"text": "x"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": 3}}}]},
            {"ruleId": "ParseError", "message": {"text": "чужой"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "D:/elsewhere/a.bsl"},
                                                 "region": {"startLine": 1}}}]}]}]}
        report = tmp / "r.sarif"
        report.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(bsl_analyzer.parse_sarif(report, root), {MOD: [{"code": "ParseError", "message": "x",
                                                                         "line": 2}]})


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, self.tmp)
        self.root = self.tmp / "cf"
        self.base = "Процедура А()\n\tНеизвестный.Метод();\nКонецПроцедуры\n"
        self.head = ("Процедура А()\n\tа = 1;\n\tНеизвестный.Метод();\n\tа = а;\n\tНеизвестный.Метод();\n"
                     "КонецПроцедуры\n")
        (self.root / MOD).parent.mkdir(parents=True)
        (self.root / MOD).write_text(self.head, encoding="utf-8-sig")
        (self.root / "Configuration.xml").write_text("<x/>", encoding="utf-8")

    def run_bsla(self, base, status="M"):
        with mock.patch.object(bsl_analyzer, "_analyze", side_effect=fake_analyze), \
                mock.patch.object(bsl_analyzer, "rule_ids", return_value=["SelfAssign", "UnresolvedMethodCall"]):
            return bsl_analyzer.run("bsl-analyzer", self.tmp / "run", self.tmp / "cache", self.root,
                            [{"path": MOD, "status": status, "reason": "changed"}], lambda rel: base,
                            lambda rel: (self.root / rel).read_text(encoding="utf-8-sig"), {}, self.tmp)

    def test_only_new(self):
        findings, info = self.run_bsla(self.base)
        got = sorted((f["check"], f["line"], f["severity"]) for f in findings)
        # один вызов был и до правки (старое семантическое замечание), второй и SelfAssign — новые
        self.assertEqual(got, [("bsla:SelfAssign", 4, "warning"), ("bsla:UnresolvedMethodCall", 5, "warning")])
        self.assertEqual(info["modules"], 1)
        self.assertEqual((self.root / MOD).read_text(encoding="utf-8-sig"), self.head)   # исходник цел
        self.assertFalse((self.tmp / "run" / "bsla" / "before").exists())

    def test_this_object_local(self):
        """В общем модуле ЭтотОбъект — локальная переменная: ThisObjectAssign не ошибка (как у BSL LS)."""
        (self.root / MOD).write_text(self.head.replace("\tа = 1;", "\tЭтотОбъект = Форма.ЭтотОбъект;"),
                                     encoding="utf-8-sig")
        findings, info = self.run_bsla(self.base)
        self.assertNotIn("bsla:ThisObjectAssign", [f["check"] for f in findings])
        self.assertEqual(info["dropped"], {"ThisObjectAssign": 1})

    def test_added_module_all_new(self):
        findings, _ = self.run_bsla(None, status="A")
        self.assertEqual(len(findings), 3)

    def test_edt_skipped(self):
        with mock.patch.object(bsl_analyzer, "_analyze", side_effect=AssertionError("не запускать")):
            findings, info = bsl_analyzer.run("x", self.tmp, self.tmp, self.root, [{"path": MOD}], None, None, {},
                                              self.tmp,
                                      lay=layout.EDT)
        self.assertEqual(findings, [])
        self.assertIn("EDT", info["skipped"])


class AdapterTest(unittest.TestCase):
    def f(self, check, line=3, severity="warning"):
        return {"check": check, "severity": severity, "module": MOD, "line": line, "message": "m"}

    def test_selected(self):
        self.assertEqual(analyzers.selected({}), (["bslls"], None))
        self.assertEqual(analyzers.selected({"analyzer": "both"})[0], ["bslls", "bsl-analyzer"])
        names, warning = analyzers.selected({"analyzer": "pmd"})
        self.assertEqual(names, ["bslls"])
        self.assertIn("pmd", warning)

    def test_selected_by_format(self):
        env = {"analyzer": {"designer-xml": "both", "edt": "bslls"}}
        self.assertEqual(analyzers.selected(env, "designer-xml"), (["bslls", "bsl-analyzer"], None))
        self.assertEqual(analyzers.selected(env, "edt"), (["bslls"], None))
        self.assertEqual(analyzers.selected({"analyzer": {"designer-xml": "both"}}, "edt"), (["bslls"], None))
        names, warning = analyzers.selected({"analyzer": {"mdo": "both"}}, "edt")
        self.assertEqual(names, ["bslls"])
        self.assertIn("mdo", warning)

    def _run(self, analyzer, lay, found=("bslls", "bsl-analyzer"), severity=None, ls_found=None):
        gate = checks.Gate({"checks": {}})
        plan = {"modules": [{"path": MOD, "reason": "changed"}], "source": "src"}
        cfg = {"environment": {"analyzer": analyzer}, "project": {}}
        if severity is not None:
            cfg["analyzerSeverity"] = severity
        ls_found = ls_found or [self.f("bslls:ParseError")]
        with mock.patch.object(analyzers, "find", side_effect=lambda n, env: n if n in found else None), \
                mock.patch.object(analyzers.revision, "read_base", return_value=lambda rel: None), \
                mock.patch.object(bsl_ls, "run", return_value=(ls_found, {"modules": 1})), \
                mock.patch.object(bsl_analyzer, "run", side_effect=AssertionError("bsl-analyzer не для EDT")):
            findings, info = analyzers.run(cfg, ".", ".", plan, ".", None, lay, None, {"objects": {}}, gate)
        return findings, info, gate.take()["notChecked"]

    def test_fail_codes_builtin(self):
        codes, note = analyzers.fail_codes()
        self.assertIsNone(note)
        self.assertEqual(codes, {"bslls": bsl_ls.FAIL_CODES, "bsl-analyzer": bsl_ls.FAIL_CODES})

    def test_fail_codes_section(self):
        codes, note = analyzers.fail_codes({"bslls": {"error": ["UsingModalWindows"], "warning": ["ThisObjectAssign"]},
                                            "bsl-analyzer": {"error": ["UnresolvedMethodCall"]}})
        self.assertIsNone(note)
        self.assertIn("UsingModalWindows", codes["bslls"])
        self.assertNotIn("ThisObjectAssign", codes["bslls"])
        self.assertNotIn("UnresolvedMethodCall", codes["bslls"])
        self.assertEqual(codes["bsl-analyzer"], bsl_ls.FAIL_CODES | {"UnresolvedMethodCall"})

    def test_fail_codes_bad_section(self):
        for section, word in (({"pmd": {"error": ["X"]}}, "pmd"), ({"bslls": ["X"]}, "таблица"),
                              ({"bslls": {"fail": ["X"]}}, "bslls.fail"), ({"bslls": {"error": "X"}}, "список"),
                              ({"bslls": {"error": ["X"], "warning": ["X"]}}, "и в error, и в warning")):
            codes, note = analyzers.fail_codes(section)
            self.assertEqual(codes["bslls"], bsl_ls.FAIL_CODES)
            self.assertIn(word, note)
            self.assertIn("раздел не учитывается", note)

    def test_severity_from_section(self):
        ls_found = [self.f("bslls:ParseError"), self.f("bslls:UsingModalWindows", 5)]
        findings, info, _ = self._run("bslls", layout.DESIGNER, ls_found=ls_found,
                                      severity={"bslls": {"error": ["UsingModalWindows"], "warning": ["ParseError"]}})
        self.assertEqual([(f["check"], f["severity"]) for f in findings],
                         [("bslls:ParseError", "warning"), ("bslls:UsingModalWindows", "error")])
        self.assertFalse(any("[analyzerSeverity]" in n for n in info.get("notes", [])))

    def test_severity_bad_section_note(self):
        findings, info, _ = self._run("bslls", layout.DESIGNER, severity={"bslls": {"error": "UsingModalWindows"}})
        self.assertEqual(findings[0]["severity"], "error")
        self.assertTrue(any("[analyzerSeverity]" in n for n in info["notes"]))

    def test_both_on_edt_falls_back(self):
        findings, info, not_checked = self._run("both", layout.EDT)
        self.assertEqual([f["check"] for f in findings], ["bslls:ParseError"])
        self.assertIn("не поддерживает формат edt", info["bsl-analyzer"]["skipped"])
        self.assertTrue(any("не поддерживает" in n for n in info["notes"]))
        self.assertEqual(not_checked, [])

    def test_explicit_unsupported_not_checked(self):
        findings, info, not_checked = self._run("bsl-analyzer", layout.EDT)
        self.assertEqual(findings, [])
        self.assertEqual([n["check"] for n in not_checked], ["analyzers"])
        self.assertIn("не поддерживает формат edt", not_checked[0]["reason"])

    def test_merge_both(self):
        merged, only = analyzers.merge({
            "bslls": [self.f("bslls:ParseError", severity="error"), self.f("bslls:LineLength", 7)],
            "bsl-analyzer": [self.f("bsla:ParseError"), self.f("bsla:UnresolvedMethodCall", 9)]})
        self.assertEqual([(f["check"], f["analyzers"]) for f in merged],
                         [("bslls:ParseError", ["bslls", "bsl-analyzer"]), ("bslls:LineLength", ["bslls"]),
                          ("bsla:UnresolvedMethodCall", ["bsl-analyzer"])])
        self.assertEqual(merged[0]["severity"], "error")
        self.assertEqual(only, {"bslls": 1, "bsl-analyzer": 1})

    def test_not_found(self):
        gate = checks.Gate({"checks": {}})
        plan = {"modules": [{"path": MOD, "reason": "changed"}]}
        cfg = {"environment": {"analyzer": "bsl-analyzer"}, "project": {}}
        with mock.patch.object(analyzers, "find", return_value=None):
            findings, info = analyzers.run(cfg, ".", ".", plan, ".", None, layout.DESIGNER, None, None, gate)
        self.assertEqual(findings, [])
        self.assertIn("OTK_BSL_ANALYZER", info["skipped"])
        self.assertIn("bsl-analyzer", gate.take()["notChecked"][0]["reason"])

    def test_both_one_missing(self):
        gate = checks.Gate({"checks": {}})
        plan = {"modules": [{"path": MOD, "reason": "changed"}], "source": "src/cf"}
        cfg = {"environment": {"analyzer": "both"}, "project": {}}
        with mock.patch.object(analyzers, "find", side_effect=lambda n, env: "ls" if n == "bslls" else None), \
                mock.patch.object(analyzers.revision, "read_base", return_value=lambda rel: None), \
                mock.patch.object(bsl_ls, "run", return_value=([self.f("bslls:ParseError")], {"modules": 1})):
            findings, info = analyzers.run(cfg, ".", ".", plan, ".", None, layout.DESIGNER, None,
                                           {"objects": {}}, gate)
        self.assertEqual([f["check"] for f in findings], ["bslls:ParseError"])
        self.assertNotIn("analyzers", findings[0])
        self.assertTrue(any("bsl-analyzer не найден" in n for n in info["notes"]))
        self.assertEqual(info["bslls"], {"modules": 1})
        self.assertEqual(gate.take()["notChecked"], [])


@unittest.skipUnless(os.environ.get("OTK_BSL_ANALYZER") and sys.platform == "win32", "нужен bsl-analyzer")
class RealAnalyzerTest(unittest.TestCase):
    """С настоящим bsl-analyzer: модель конфигурации видит методы других общих модулей."""

    def test_real(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, tmp)
        root = tmp / "cf"
        ns = 'xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17"'
        conf = (f'<?xml version="1.0" encoding="UTF-8"?><MetaDataObject {ns}>'
                '<Configuration uuid="0e1a5b5e-0000-4000-8000-000000000001"><Properties><Name>К</Name></Properties>'
                '<ChildObjects><CommonModule>Общий</CommonModule><CommonModule>Другой</CommonModule>'
                '</ChildObjects></Configuration></MetaDataObject>')
        root.mkdir()
        (root / "Configuration.xml").write_text(conf, encoding="utf-8")
        flags = {"Global": "false", "ClientManagedApplication": "false", "Server": "true",
                 "ExternalConnection": "false", "ClientOrdinaryApplication": "false", "ServerCall": "false",
                 "Privileged": "false"}
        for n, name in enumerate(("Общий", "Другой")):
            (root / "CommonModules" / name / "Ext").mkdir(parents=True)
            props = "".join(f"<{k}>{v}</{k}>" for k, v in flags.items())
            (root / "CommonModules" / f"{name}.xml").write_text(
                f'<?xml version="1.0" encoding="UTF-8"?><MetaDataObject {ns}>'
                f'<CommonModule uuid="0e1a5b5e-0000-4000-8000-00000000001{n}"><Properties><Name>{name}</Name>'
                f'{props}<ReturnValuesReuse>DontUse</ReturnValuesReuse></Properties></CommonModule>'
                '</MetaDataObject>', encoding="utf-8")
        (root / "CommonModules/Другой/Ext/Module.bsl").write_text(
            "Процедура Есть() Экспорт\nКонецПроцедуры\n", encoding="utf-8-sig")
        head = "Процедура А() Экспорт\n\tДругой.Есть();\n\tДругой.НетТакого();\nКонецПроцедуры\n"
        (root / MOD).write_text(head, encoding="utf-8-sig")
        findings, info = bsl_analyzer.run(os.environ["OTK_BSL_ANALYZER"], tmp / "run", tmp / "cache", root,
                                  [{"path": MOD, "status": "M", "reason": "changed"}],
                                  lambda rel: "Процедура А() Экспорт\nКонецПроцедуры\n",
                                  lambda rel: head, {}, tmp)
        self.assertNotIn("error", info)
        unresolved = [f["line"] for f in findings if f["check"] == "bsla:UnresolvedMethodCall"]
        self.assertEqual(unresolved, [3])


class ChangedFilesTest(unittest.TestCase):
    def cmd(self, rels):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bsl_analyzer.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout=b"", stderr=b"Error: test")
            bsl_analyzer._analyze("bsla.exe", tmp, rels, "c.toml", Path(tmp) / "cache", Path(tmp) / "out")
            return run.call_args[0][0]

    def test_listed(self):
        cmd = self.cmd([MOD])
        self.assertEqual(cmd[cmd.index("--changed-files") + 1], MOD)

    def test_whole_tree_when_too_long(self):
        """Вся конфигурация (УНФ — 15 тыс. модулей) в командную строку Windows не помещается: WinError 206."""
        cmd = self.cmd([f"CommonModules/Модуль{i}/Ext/Module.bsl" for i in range(2000)])
        self.assertNotIn("--changed-files", cmd)
        self.assertLess(len(" ".join(cmd)), 32767)


if __name__ == "__main__":
    unittest.main()
