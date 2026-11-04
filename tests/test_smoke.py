"""Дымовой тест форм: отбор изменённых форм, исключения, ошибки обработчиков из журнала, зависшая форма."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.gate import collect, pipeline
from otk_1c.probes.executors import yaxunit

STACK = ("Деление на 0\n{ext ОбщаяФорма.Настройки.Форма(193)}:Итог = 1 / Делитель;\n"
         "{otk ОбщийМодуль.otk_ТестыПроб.Модуль(39)}:Форма.Открыть();")


class SmokeTest(unittest.TestCase):
    def test_changed_forms(self):
        modules = [{"kind": "FormModule", "owner": "ОбщаяФорма.А", "path": "CommonForms/А/Ext/Form/Module.bsl",
                    "probe": {"strategy": "get-form"}},
                   {"kind": "CommonModule", "owner": "ОбщийМодуль.М", "path": "CommonModules/М/Ext/Module.bsl",
                    "probe": {"strategy": "missing-method"}}]
        metadata = [{"path": "CommonForms/А/Ext/Form.xml", "status": "M"},                      # уже есть по модулю
                    {"path": "Catalogs/К/Forms/ФормаСписка/Ext/Form.xml", "status": "M"},        # только разметка
                    {"path": "Catalogs/К/Forms/Удалённая/Ext/Form.xml", "status": "D"},
                    {"path": "Catalogs/К.xml", "status": "M"}]
        files = {"Catalogs/К/Forms/ФормаСписка/Ext/Form/Module.bsl": ""}
        forms = collect.changed_forms(modules, metadata, files.get)
        self.assertEqual([(f["form"], f["reason"]) for f in forms],
                         [("ОбщаяФорма.А", "module"), ("Справочник.К.Форма.ФормаСписка", "layout")])
        self.assertEqual(forms[1]["path"], "Catalogs/К/Forms/ФормаСписка/Ext/Form/Module.bsl")
        files["CommonForms/А.xml"] = "<Form><Properties><FormType>Ordinary</FormType></Properties></Form>"
        self.assertEqual([f["form"] for f in collect.changed_forms(modules, metadata, files.get)],
                         ["Справочник.К.Форма.ФормаСписка"])        # обычная форма в тонком клиенте не открывается

    def test_smoke_forms_config(self):
        forms = [{"form": "Обработка.Загрузка.Форма.Форма"}, {"form": "Справочник.К.Форма.ФормаСписка"}]
        self.assertEqual(pipeline.smoke_forms(forms, {}), forms)
        self.assertEqual(pipeline.smoke_forms(forms, {"exclude": ["обработка.*"]}), forms[1:])

    def test_form_error(self):
        self.assertEqual(yaxunit.form_error([STACK], "ОбщаяФорма.Настройки"),
                         (193, "Деление на 0 {ext ОбщаяФорма.Настройки.Форма(193)}"))
        self.assertIsNone(yaxunit.form_error([STACK], "ОбщаяФорма.Другая"))
        self.assertIsNone(yaxunit.form_error([STACK.split("\n{otk")[0]], "ОбщаяФорма.Настройки"))  # не из пробы

    def test_run_smoke_hung_form(self):
        """Форма не закрылась: она — ошибка, пройденные до неё — по журналу хода, остальные — новым запуском."""
        tmp = Path(tempfile.mkdtemp())
        forms = ["ОбщаяФорма.А", "ОбщаяФорма.Настройки", "ОбщаяФорма.Б", "ОбщаяФорма.В"]
        items = yaxunit.build_smoke([{"form": f, "path": f, "reason": "module"} for f in forms])
        launches = []

        def fake_launch(platform, ws, filters, timeout=600, stalled=None):
            names = [i[2]["expr"] for i in launches_plan(ws)]
            launches.append(names)
            if len(launches) == 1:                # А открылась (с ошибкой в журнале), Настройки зависла
                (ws / "smoke-progress.txt").write_text("start|ОбщаяФорма.А\ndone|ОбщаяФорма.А\n"
                                                       "start|ОбщаяФорма.Настройки\n", encoding="utf-8")
                err = STACK.replace("Настройки", "А")
                return ws / "report.xml", None, [err], ""
            (ws / "smoke-progress.txt").write_text("start|ОбщаяФорма.Б\ndone|ОбщаяФорма.Б\nstart|ОбщаяФорма.В\n"
                                                   "skip|ОбщаяФорма.В\n", encoding="utf-8")
            return ws / "report.xml", 0, [], ""   # отчёта нет (клиент упал после всех форм) — по журналу хода

        def launches_plan(ws):
            import json
            names = json.loads((ws / "yax-plan.json").read_text(encoding="utf-8"))
            return [(None, None, n) for n in names]

        try:
            with mock.patch.object(yaxunit, "launch", fake_launch):
                res, _ = yaxunit.run_smoke(None, items, tmp, form_timeout=5)
        finally:
            rmtree_force(tmp)
        self.assertEqual(launches, [forms, forms[2:]])
        self.assertEqual([(r["owner"], r["outcome"]) for r in res],
                         [("ОбщаяФорма.А", "failed"), ("ОбщаяФорма.Настройки", "failed"),
                          ("ОбщаяФорма.Б", "ok"), ("ОбщаяФорма.В", "skipped")])
        self.assertIn("не закрылась", res[1]["message"])
        self.assertEqual(res[0]["line"], 193)

    def test_progress_busy(self):
        """Файл хода занят клиентом (совместный доступ): опрос не падает и не считает форму зависшей."""
        tmp = Path(tempfile.mkdtemp())
        items = yaxunit.build_smoke([{"form": "ОбщаяФорма.А", "path": "ОбщаяФорма.А", "reason": "module"}])
        seen = []

        def fake_launch(platform, ws, filters, timeout=600, stalled=None):
            (ws / "smoke-progress.txt").write_text("start|ОбщаяФорма.А\n", encoding="utf-8")
            real = Path.read_text

            def busy(self, *a, **k):
                if self.name == "smoke-progress.txt":
                    raise PermissionError(32, "процесс не может получить доступ к файлу")
                return real(self, *a, **k)
            with mock.patch.object(Path, "read_text", busy):
                seen.append(stalled())
            (ws / "smoke-progress.txt").write_text("start|ОбщаяФорма.А\ndone|ОбщаяФорма.А\n", encoding="utf-8")
            return ws / "report.xml", 0, [], ""

        try:
            with mock.patch.object(yaxunit, "launch", fake_launch):
                res, _ = yaxunit.run_smoke(None, items, tmp, form_timeout=0)
        finally:
            rmtree_force(tmp)
        self.assertEqual(seen, [False])
        self.assertEqual([(r["owner"], r["outcome"]) for r in res], [("ОбщаяФорма.А", "ok")])


if __name__ == "__main__":
    unittest.main()
