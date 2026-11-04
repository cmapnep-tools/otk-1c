"""Каталог контрольных дефектов: пакеты без общих файлов и выбор модулей для небольших проектов."""
import collections
import sqlite3
import unittest

from otk_1c.extras import controls


def defect(id_, changed, files=None):
    d = controls.Defect(id_, id_, ["static"])
    d.changes = {f"src/{p}": "текст" for p in changed}
    d.files = list(files if files is not None else changed)
    return d


class BatchesTest(unittest.TestCase):
    def test_one_batch_when_files_differ(self):
        packs = controls.batches([defect("a", ["A.bsl"]), defect("b", ["B.bsl"])], "src")
        self.assertEqual(len(packs), 1)
        self.assertEqual(set(packs[0][1]), {"src/A.bsl", "src/B.bsl"})

    def test_shared_module_splits(self):
        """Платформа сообщает только первую ошибку компиляции модуля — дефекты одного модуля в разных пакетах."""
        packs = controls.batches([defect("a", ["A.bsl"]), defect("b", ["A.bsl"]), defect("c", ["B.bsl"]),
                                  defect("d", ["A.bsl"])], "src")
        self.assertEqual([[d.id for d in ds] for ds, _ in packs], [["a", "c"], ["b"], ["d"]])

    def test_checked_files_count(self):
        """Удалённый метод проверяется по вызывающим: их нельзя портить в том же пакете."""
        removed = defect("removed", ["M.bsl"], files=["Caller.bsl"])
        packs = controls.batches([removed, defect("other", ["Caller.bsl"])], "src")
        self.assertEqual(len(packs), 2)

    def test_pick_reuses_least_used(self):
        used, uses = set(), collections.Counter()
        picks = [controls._pick(["A", "B"], used, uses) for _ in range(5)]
        self.assertEqual(picks, ["A", "B", "A", "B", "A"])
        self.assertIsNone(controls._pick([], set(), collections.Counter()))
        self.assertIsNone(controls._pick(["A"], {"A"}))       # без счётчика — прежнее поведение


class FakeIndex:
    """Индекс справки в памяти: таблица entries с теми полями, что читает controls._newer_api."""
    opened = []                                          # закрывает NewerApiTest.tearDown

    def __init__(self, rows):
        from otk_1c.probes.syntax.index import GLOBAL, version_key
        self.db = sqlite3.connect(":memory:")
        self.opened.append(self.db)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE entries(kind, owner, name, syntax, availability, since, since_key)")
        for kind, name, syntax, avail, since in rows:
            self.db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?)",
                            (kind, GLOBAL if kind == "method" else "", name, syntax, avail, since, version_key(since)))

    def lookup(self, name, kind=None):
        return self.db.execute("SELECT * FROM entries WHERE name = ? AND kind = ?", (name, kind)).fetchall()


class NewerApiTest(unittest.TestCase):
    def tearDown(self):
        while FakeIndex.opened:
            FakeIndex.opened.pop().close()

    ROWS = [("method", "СтарыйМетод", "СтарыйМетод()", "Сервер.", "8.3.20"),
            ("method", "КлиентскийНовый", "КлиентскийНовый()", "Тонкий клиент, мобильное приложение (сервер).",
             "8.3.25"),
            ("method", "ВызватьПаузу", "ВызватьПаузу(<Интервал>)", "Сервер, толстый клиент.", "8.3.25"),
            ("method", "Палитра", "Палитра()", "Тонкий клиент, сервер, внешнее соединение.", "8.5.1"),
            ("ctor", "Генератор", "Новый Генератор()", "", "8.3.22"),
            ("type", "Генератор", "", "Сервер, толстый клиент.", "8.3.22"),
            ("ctor", "СПараметром", "Новый СПараметром(<Имя>)", "", "8.3.22"),
            ("type", "СПараметром", "", "Сервер.", "8.3.22")]

    def test_targets(self):
        got = {did: (row["name"], sev) for did, _, row, sev in
               controls._newer_api((FakeIndex(self.ROWS), "Version8_3_21"))}
        self.assertEqual(got, {"newer-line-method": ("Палитра", "error"), "newer-method": ("ВызватьПаузу", "warning"),
                               "newer-ctor": ("Генератор", "warning")})

    def test_hidden_warnings_not_planted(self):
        """Предупреждение, которое настройка скрывает, контрольным дефектом не вносится; ошибка — вносится."""
        api = (FakeIndex(self.ROWS), "Version8_3_21", lambda since: False)
        self.assertEqual([d for d, *_ in controls._newer_api(api)], ["newer-line-method"])
        api = (FakeIndex(self.ROWS), "Version8_3_21", lambda since: since > "8.3.24")
        self.assertEqual([d for d, *_ in controls._newer_api(api)], ["newer-line-method", "newer-method"])

    def test_no_compat_or_help(self):
        self.assertEqual(controls._newer_api(None), [])
        self.assertEqual(controls._newer_api((FakeIndex(self.ROWS), None)), [])
        # платформа той же линейки, что режим: метода новее линейки нет
        rows = [r for r in self.ROWS if r[1] != "Палитра"]
        self.assertNotIn("newer-line-method", [d for d, *_ in controls._newer_api((FakeIndex(rows), "Version8_3_21"))])


class EvaluateTest(unittest.TestCase):
    @staticmethod
    def summary(severity):
        return {"findings": [{"severity": severity, "file": "src/M.bsl", "step": "static", "code": "platform-api"}]}

    def test_warning_defect(self):
        d = controls.Defect("newer-ctor", "t", ["static:platform-api"], severity="warning")
        d.files = ["M.bsl"]
        self.assertTrue(controls.evaluate([d], self.summary("warning"))[0][1])
        self.assertTrue(controls.evaluate([d], self.summary("error"))[0][1])
        e = controls.Defect("x", "t", ["static:platform-api"])
        e.files = ["M.bsl"]
        self.assertFalse(controls.evaluate([e], self.summary("warning"))[0][1])

    def test_designer_hits_by_owner(self):
        d = controls.Defect("form-missing-proc", "t", [], "ОТКНетПроцедурыФормы")
        d.owner = "Справочник.Товары.Форма.Ф"
        out = ("{otk_check Справочник.Товары.Форма.Ф.Форма(12,2)}: Процедура или функция с указанным именем "
               "не определена (ОТКНетПроцедурыФормы)\n")
        self.assertEqual(controls.designer_hits([d], out), {"form-missing-proc": True})
        self.assertEqual(controls.designer_hits([d], out.replace(".Ф.", ".Другая.")), {"form-missing-proc": False})

    def test_removed_export_by_callers(self):
        """Удалённый экспорт пойман, только если ошибка — у вызывающего модуля, а не внутри своего."""
        d = controls.Defect("removed-export", "t", [], "Метод")
        d.owner = "ОбщийМодуль.Свой"
        d.files = ["CommonModules/Вызов/Ext/Module.bsl"]
        own = "{ОбщийМодуль.Свой.Модуль(1,1)}: Процедура или функция с указанным именем не определена (Метод)\n"
        caller = 'ОбщийМодуль.Вызов.Модуль Возможно ошибочный метод: "Метод"\n'
        self.assertEqual(controls.designer_hits([d], own), {"removed-export": False})
        self.assertEqual(controls.designer_hits([d], own + caller), {"removed-export": True})


class DocumentTest(unittest.TestCase):
    COND = {"configuration": "Демо", "version": "1.0", "platform": "8.3.27.2342", "compat": "Version8_3_21",
            "modes": "-Server -ExtendedModulesCheck", "commit": "abc", "date": "2026-10-19"}

    def rows(self):
        a = controls.Defect("removed-export", "удалён экспорт", ["static:dependent"])
        b = controls.Defect("query-field", "поле запроса", ["run:com"])
        c = controls.Defect("newer-ctor", "конструктор", ["static:platform-api"], severity="warning")
        return [(a, True, ["static:dependent"]), (b, True, ["run:com"]), (c, True, ["static:platform-api"])]

    def test_two_references(self):
        hits = {"removed-export": True, "query-field": False, "newer-ctor": False}
        ext = {"removed-export": False, "query-field": False, "newer-ctor": False}
        doc = controls.document(self.rows(), hits, ext, [("form-hang", "нет формы")], self.COND)
        self.assertIn("| Дефект | ОТК | Шаги ОТК | Конфигуратор: синтаксический контроль (`/CheckModules`) | "
                      "В расширении (`/CheckModules -Extension otk_check`) |", doc)
        self.assertNotIn("полная проверка (", doc)
        self.assertIn("| удалён экспорт | да | `static:dependent` | да | нет |", doc)
        self.assertIn("да (предупреждение)", doc)
        self.assertIn("## Расхождение эталонов", doc)
        self.assertIn("удалён экспорт: основная — да, в расширении — нет. В расширение заимствуется", doc)
        self.assertIn("- `form-hang` — нет формы", doc)
        self.assertIn("поле запроса [^1]", doc)
        self.assertIn("[^1]: текст запроса", doc)

    def test_full_check_and_time(self):
        hits = {"removed-export": True, "query-field": False, "newer-ctor": False}
        ext = {"removed-export": False, "query-field": False, "newer-ctor": False}
        full = {"removed-export": True, "query-field": False, "newer-ctor": False}
        times = {"gate": [60, 80], "modules": [10, 12], "config": [30, 31]}
        doc = controls.document(self.rows(), hits, ext, [], {**self.COND, "configFlags": "-HandlersExistence"},
                                full, times)
        self.assertIn("otk_check`) | Конфигуратор: полная проверка (`/CheckConfig`) |", doc)
        self.assertIn("- Флаги `/CheckConfig`: `-HandlersExistence`", doc)
        self.assertIn("| удалён экспорт | да | `static:dependent` | да | нет | да |", doc)
        self.assertIn("| **время на пакет, с** [^3] | 70 | — | 11 | — | 30 |", doc)
        self.assertIn("[^3]: среднее по пакетам дефектов (2)", doc)

    def test_extension_project(self):
        doc = controls.document(self.rows(), {}, None, [], {**self.COND, "extension": "demo_ext"})
        self.assertNotIn("В расширении", doc)
        self.assertNotIn("Расхождение", doc)
        self.assertIn("расширение `demo_ext`", doc)
        self.assertIn("синтаксический контроль (`/CheckModules -Extension`) |", doc)
        self.assertIn("| удалён экспорт | да | `static:dependent` | — |", doc)


class AnalyzersTest(unittest.TestCase):
    """controls --analyzers: кто из анализаторов что ловит, по находкам static.json."""
    STATIC = {"analyzerInfo": {"analyzer": "both", "bslls": {"modules": 2}, "bsl-analyzer": {"modules": 2}},
              "findings": [
                  {"check": "bslls:ParseError", "severity": "error", "module": "src/A.bsl",
                   "analyzers": ["bslls", "bsl-analyzer"]},
                  {"check": "bsla:UnreachableCode", "severity": "warning", "module": "src/A.bsl",
                   "analyzers": ["bsl-analyzer"]},
                  {"check": "bsla:MismatchedArgCount", "severity": "warning", "module": "src/B.bsl",
                   "analyzers": ["bsl-analyzer"]},
                  {"check": "common-method", "severity": "error", "module": "src/B.bsl"},
                  {"check": "bslls:ParseError", "severity": "error", "module": "src/C.bsl"}]}

    def defects(self):
        a, b = defect("syntax", ["A.bsl"]), defect("extra-arg", ["B.bsl"])
        c = controls.Defect("no-return", "без возврата", controls.ANALYZER_EXPECT)
        c.files = ["D.bsl"]
        return [a, b, c]

    def test_hits(self):
        hits = controls.analyzer_hits(self.defects(), self.STATIC)
        self.assertEqual(hits["syntax"], {"bslls": {"ParseError": "error"},
                                          "bsl-analyzer": {"ParseError": "error", "UnreachableCode": "warning"},
                                          "otk": []})
        self.assertEqual(hits["extra-arg"], {"bslls": {}, "bsl-analyzer": {"MismatchedArgCount": "warning"},
                                             "otk": ["common-method"]})
        self.assertEqual(hits["no-return"], {"bslls": {}, "bsl-analyzer": {}, "otk": []})

    def test_single_analyzer_by_prefix(self):
        d = defect("c", ["C.bsl"])
        self.assertEqual(controls.analyzer_hits([d], self.STATIC)["c"]["bslls"], {"ParseError": "error"})

    def test_status(self):
        self.assertEqual(controls.analyzer_status(self.STATIC), {"bslls": None, "bsl-analyzer": None})
        info = {"analyzerInfo": {"bslls": {"modules": 1}, "bsl-analyzer": {"skipped": "bsl-analyzer не найден"}}}
        self.assertEqual(controls.analyzer_status(info), {"bslls": None, "bsl-analyzer": "bsl-analyzer не найден"})
        self.assertTrue(all(controls.analyzer_status({"analyzerInfo": {"off": True}}).values()))

    def test_document(self):
        ds = self.defects()
        hits = controls.analyzer_hits(ds, self.STATIC)
        cond = {"configuration": "Демо", "version": "1.0", "commit": "abc", "date": "2026-10-21",
                "analyzers": {"bslls": "1.0", "bsl-analyzer": None}}
        doc = controls.analyzers_document([(d, hits[d.id]) for d in ds], {"bslls": None, "bsl-analyzer": None},
                                          [("client-modal", "нет клиентского общего модуля")], cond)
        self.assertIn("- Анализаторы: BSL LS — 1.0; bsl-analyzer — не найден", doc)
        self.assertIn("| syntax | `static` | `ParseError` (error) | `ParseError` (error), "
                      "`UnreachableCode` (warning) | — |",
                      doc)
        self.assertIn("| extra-arg | `static` | нет | `MismatchedArgCount` (warning) | `common-method` |", doc)
        self.assertIn("| без возврата [^1] | анализаторы | нет | нет | — |", doc)
        self.assertIn("- extra-arg: только bsl-analyzer (`MismatchedArgCount`)", doc)
        self.assertNotIn("- syntax: только", doc)
        self.assertIn("Из 3 дефектов BSL LS поймал 1, bsl-analyzer — 2, статика ОТК без анализаторов — 1", doc)
        self.assertIn("- `client-modal` — нет клиентского общего модуля", doc)
        self.assertIn("[^1]: `AllFunctionPathMustHaveReturn` BSL LS", doc)

    def test_not_checked(self):
        ds = self.defects()[:1]
        doc = controls.analyzers_document([(ds[0], controls.analyzer_hits(ds, self.STATIC)["syntax"])],
                                          {"bslls": None, "bsl-analyzer": "не найден"}, [],
                                          {"configuration": "Демо", "version": "1.0", "commit": "abc",
                                           "date": "2026-10-21", "analyzers": {"bslls": "1.0", "bsl-analyzer": None}})
        self.assertIn("| syntax | `static` | `ParseError` (error) | не проверено | — |", doc)
        self.assertIn("bsl-analyzer не проверено: не найден", doc)


if __name__ == "__main__":
    unittest.main()
