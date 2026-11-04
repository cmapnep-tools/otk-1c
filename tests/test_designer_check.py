"""Полная проверка Конфигуратором и запуск в CI: разбор вывода, контрольный модуль, настройки, артефакты."""
import json
import tempfile
import unittest
import xml.dom.minidom
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import config
from otk_1c.probes import designer_check
from otk_1c.probes.platform import ConfigOps

OUTPUT = (
    "{ОбщийМодуль.КассовыеСмены.Модуль(373,10)}: Переменная не определена (Икс)\r\n"
    "\tВозврат <<?>>Икс; (Проверка: Сервер)\r\n"
    "{ОбщийМодуль.КассовыеСмены.Модуль(373,10)}: Переменная не определена (Икс)\r\n"
    "\tВозврат <<?>>Икс; (Проверка: Внешнее соединение)\r\n"
    "{Документ.Событие.Форма.ФормаДокумента.Форма(12,3)}: Ожидается выражение\r\n"
    "\tА =<<?>> ; (Проверка: Тонкий клиент)\r\n"
    "{Справочник.Клиенты.МодульМенеджера(5,1)}: Процедура или функция с указанным именем не определена (Х)\r\n"
    "{ОбщаяФорма.Настройки.Форма(1,1)}: Ожидается выражение\r\n"
    "{Документ.Событие.Команда.Печать.МодульКоманды(2,2)}: Ожидается выражение\r\n"
    "{Конфигурация.МодульСеанса(7,7)}: Ожидается выражение\r\n"
    "{ОбщийМодуль.ЖурналКлиент.Модуль(35,7)}: Переменная не определена (Сообщения)\r\n"
    "\tЕсли <<?>>Сообщения = Неопределено Тогда (Проверка: Толстый клиент (обычное приложение))\r\n"
    "Обычная строка без позиции\r\n"
)

FLAGS = ("<CommonModule><Properties><Global>{g}</Global><ClientManagedApplication>{c}</ClientManagedApplication>"
         "<Server>{s}</Server></Properties></CommonModule>")


class DesignerCheckTest(unittest.TestCase):
    def test_parse(self):
        found = designer_check.parse(OUTPUT, "src/cf")
        self.assertEqual(len(found), 7)                     # одинаковая ошибка двух режимов — одна запись
        self.assertEqual(found[0]["modes"], ["Сервер", "Внешнее соединение"])
        self.assertEqual(found[0]["file"], "src/cf/CommonModules/КассовыеСмены/Ext/Module.bsl")
        files = [f["file"] for f in found]
        self.assertIn("src/cf/Documents/Событие/Forms/ФормаДокумента/Ext/Form/Module.bsl", files)
        self.assertIn("src/cf/Catalogs/Клиенты/Ext/ManagerModule.bsl", files)
        self.assertIn("src/cf/CommonForms/Настройки/Ext/Form/Module.bsl", files)
        self.assertIn("src/cf/Documents/Событие/Commands/Печать/Ext/CommandModule.bsl", files)
        self.assertIsNone(found[-2]["file"])                # имя модуля не сопоставлено — остаётся объект
        self.assertEqual(found[-2]["object"], "Конфигурация.МодульСеанса")
        self.assertEqual(found[-1]["modes"], ["Толстый клиент (обычное приложение)"])   # скобки в имени режима
        self.assertEqual(found[-1]["code"], "Если Сообщения = Неопределено Тогда")
        self.assertEqual(found[0]["code"], "Возврат Икс;")

    def test_baseline(self):
        found = designer_check.parse(OUTPUT, "src/cf")
        tmp = Path(tempfile.mkdtemp())
        try:
            path = tmp / designer_check.BASELINE
            self.assertIsNone(designer_check.load_baseline(path))
            self.assertEqual(designer_check.split_new(found, None), (found, [], 0))
            designer_check.save_baseline(path, found, "abc")
            base = designer_check.load_baseline(path)
            self.assertEqual(designer_check.split_new(found, base), ([], found, 0))
            # та же ошибка ниже по модулю (правка выше) — не новая; ещё одна такая же — новая; исправленная — учтена
            moved = [{**found[0], "line": found[0]["line"] + 10}] + found[1:-1] + [dict(found[0])]
            new, known, fixed = designer_check.split_new(moved, base)
            self.assertEqual(len(new), 1)
            self.assertEqual(fixed, 1)
        finally:
            rmtree_force(tmp)

    def test_control_module(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cm = tmp / "CommonModules"
            for name, g, c, s in (("АКлиент", "false", "true", "false"), ("БГлобальный", "true", "false", "true"),
                                  ("ВСервер", "false", "false", "true")):
                (cm / name / "Ext").mkdir(parents=True)
                (cm / name / "Ext" / "Module.bsl").write_text("", encoding="utf-8")
                (cm / f"{name}.xml").write_text(FLAGS.format(g=g, c=c, s=s), encoding="utf-8")
            self.assertEqual(designer_check.control_module(tmp), "ВСервер")
            (cm / "ОбщегоНазначения" / "Ext").mkdir(parents=True)
            (cm / "ОбщегоНазначения" / "Ext" / "Module.bsl").write_text("", encoding="utf-8")
            (cm / "ОбщегоНазначения.xml").write_text(FLAGS.format(g="false", c="false", s="true"), encoding="utf-8")
            self.assertEqual(designer_check.control_module(tmp), "ОбщегоНазначения")
        finally:
            rmtree_force(tmp)

    def test_reports(self):
        s = {"code": 1, "commit": "a" * 40, "modes": ["-Server"], "control": {"ok": True, "detail": "ок"},
             "findings": designer_check.parse(OUTPUT, "src/cf"), "timings": {"check": 1.0}, "notes": []}
        md = designer_check.markdown(s)
        self.assertTrue(md.startswith("# ОШИБКИ: полная проверка Конфигуратором — 7"))
        self.assertIn("`src/cf/CommonModules/КассовыеСмены/Ext/Module.bsl:373`", md)
        doc = xml.dom.minidom.parseString(designer_check.junit(s))
        self.assertEqual(doc.documentElement.getAttribute("failures"), "7")
        s2 = {**s, "code": 2, "findings": [], "notes": ["проверка Конфигуратором не работает: x"]}
        self.assertIn("СБОЙ: проверка Конфигуратором не работает", designer_check.markdown(s2))
        doc2 = xml.dom.minidom.parseString(designer_check.junit(s2))
        self.assertEqual(doc2.documentElement.getAttribute("errors"), "1")


class FakePlatform(ConfigOps):
    """Конфигуратор-заглушка: ответ /CheckModules -Server задаёт тест."""
    def __init__(self, check):
        self.check, self.calls = check, []

    def designer(self, *args, label="", timeout=0):
        self.calls.append(label)
        if label == "control-check":
            return self.check
        return 0, 0.1, ""


MAIN_HIT = "{ОбщийМодуль.ОТККонтроль.Модуль(2,5)}: Ожидается выражение\r\n\tА =<<?>> ; (Проверка: Сервер)\r\n"
EXT_HIT = ("{otkc_control ОбщийМодуль.otkc_Контроль.Модуль(2,5)}: Ожидается выражение\r\n"
           "\tА =<<?>> ; (Проверка: Сервер)\r\n")


class MinimalControlTest(unittest.TestCase):
    """Вариант А: ИБ контроля — ошибка находится и в основной конфигурации, и с -Extension."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = mock.patch.dict("os.environ", {"OTK_CACHE": str(self.tmp / "cache")})
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        rmtree_force(self.tmp)

    def platform(self, answers):
        p = MiniPlatform(answers)
        p.ib, p.run_dir = self.tmp / "ib", self.tmp / "run"
        return p

    def test_created_once_and_caught(self):
        p = self.platform({"control-check": (101, 1.0, MAIN_HIT), "control-check-ext": (101, 1.0, EXT_HIT)})
        ok, detail = designer_check.minimal_control(p, self.tmp / "run", log=lambda m: None)
        self.assertTrue(ok, detail)
        root = designer_check.control_ib_dir(p)
        self.assertEqual(root.name, "8.3.27.2342")
        self.assertTrue((root / "ready").is_file())
        self.assertEqual([c for c in p.calls if c.startswith("control-ib")],
                         ["control-ib-dump", "control-ib-load", "control-ib-update", "control-ib-ext"])
        dump_cfg = (self.tmp / "cache").glob("control/*.tmp")
        self.assertEqual(list(dump_cfg), [])                               # временный каталог убран
        label, ib, user, args = p.args[-1]
        self.assertEqual((label, ib, user), ("control-check-ext", root / "ib", ""))   # своя ИБ, без пользователя
        self.assertEqual(args, ("/CheckModules", "-Server", "-Extension", designer_check.CONTROL_EXT))
        p.calls.clear()
        self.assertTrue(designer_check.minimal_control(p, self.tmp / "run", log=lambda m: None)[0])
        self.assertEqual(p.calls, ["control-check", "control-check-ext"])  # второй раз — из кэша

    def test_extension_not_caught_resets_cache(self):
        p = self.platform({"control-check": (101, 1.0, MAIN_HIT), "control-check-ext": (0, 1.0, "")})
        ok, detail = designer_check.minimal_control(p, self.tmp / "run", log=lambda m: None)
        self.assertFalse(ok)
        self.assertIn("расширение: rc=0, ошибка НЕ найдена", detail)
        self.assertFalse(designer_check.control_ib_dir(p).exists())

    def test_not_created(self):
        p = self.platform({})
        p.create_rc = 1
        with self.assertRaisesRegex(designer_check.InfraError, "нет лицензии"):
            designer_check.minimal_control(p, self.tmp / "run", log=lambda m: None)
        self.assertEqual(list((self.tmp / "cache" / "control").iterdir()), [])

    def test_parallel_ready_kept_and_orphans_removed(self):
        p = self.platform({"control-check": (101, 1.0, MAIN_HIT), "control-check-ext": (101, 1.0, EXT_HIT)})
        root = designer_check.control_ib_dir(p)
        orphan = root.with_name(f"{root.name}.999999.tmp")
        orphan.mkdir(parents=True)
        real_write = designer_check.ib_tools.generate_test_extension

        def other_won(*a, **k):                          # пока создаём — параллельный запуск опубликовал свою
            (root / "ib").mkdir(parents=True)
            (root / "ready").write_text("x", encoding="utf-8")
            return real_write(*a, **k)
        with mock.patch.object(designer_check.ib_tools, "generate_test_extension", other_won), \
                mock.patch.object(designer_check, "pid_alive", lambda pid: pid != 999999):
            ib = designer_check.control_ib(p, log=lambda m: None)
        self.assertEqual(ib, root / "ib")
        self.assertEqual((root / "ready").read_text(encoding="utf-8"), "x")   # чужая ИБ не стёрта
        self.assertEqual(sorted(x.name for x in root.parent.iterdir()), [root.name])  # свой и осиротевший tmp убраны

    def test_broken_dump_falls_back_to_inline(self):
        """Любой сбой ИБ контроля (здесь — выгрузка без InterfaceCompatibilityMode) — вариант Б, не трейсбек."""
        p = self.platform({"designer-check": (101, 5.0, "{ОбщийМодуль.Общий.Модуль(5,5)}: Ожидается выражение\r\n")})
        real = p.designer

        def designer(*args, label="", timeout=0):
            res = real(*args, label=label, timeout=timeout)
            if label == "control-ib-dump":
                cfg = Path(args[1]) / "Configuration.xml"
                cfg.write_text(cfg.read_text(encoding="utf-8-sig").replace("InterfaceCompatibilityMode", "X"),
                               encoding="utf-8-sig")
            return res
        p.designer = designer
        dump = self.tmp / "src"
        (dump / "CommonModules" / "Общий" / "Ext").mkdir(parents=True)
        (dump / "CommonModules" / "Общий" / "Ext" / "Module.bsl").write_text("Процедура А()\nКонецПроцедуры\n",
                                                                            encoding="utf-8")
        (dump / "CommonModules" / "Общий.xml").write_text(FLAGS.format(g="false", c="false", s="true"),
                                                          encoding="utf-8")
        res = designer_check.check(p, dump, "src", ["-Server"], self.tmp / "b.json", self.tmp / "run",
                                   log=lambda m: None)
        self.assertEqual((res["code"], res["control"]["variant"], res["control"]["ok"]), (0, "B", True))
        self.assertIn("ИБ контроля недоступна", res["control"]["detail"])


class ControlTest(unittest.TestCase):
    """check(): контроль А, при его неудаче — Б внутри полной проверки (ошибка в самой копии)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dump = self.tmp / "src"
        cm = self.dump / "CommonModules"
        (cm / "Общий" / "Ext").mkdir(parents=True)
        (cm / "Общий" / "Ext" / "Module.bsl").write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8")
        (cm / "Общий.xml").write_text(FLAGS.format(g="false", c="false", s="true"), encoding="utf-8")
        self.minimal = mock.patch.object(designer_check, "minimal_control",
                                         side_effect=designer_check.InfraError("ИБ контроля не создаётся"))
        self.minimal.start()

    def tearDown(self):
        self.minimal.stop()
        rmtree_force(self.tmp)

    def check(self, answers):
        p = Platform2(answers)
        p.ib = self.tmp / "ib"
        res = designer_check.check(p, self.dump, "src", ["-Server"], self.tmp / "baseline.json", self.tmp / "run",
                                   log=lambda m: None)
        return res, p

    def test_minimal_ok_no_inline(self):
        with mock.patch.object(designer_check, "minimal_control", return_value=(True, "ИБ контроля — ок")):
            res, p = self.check({"designer-check": (0, 5.0, "")})
        self.assertEqual((res["code"], res["control"]["variant"], res["control"]["ok"]), (0, "A", True))
        self.assertEqual(p.calls, ["designer-check"])

    def test_inline_caught_and_excluded(self):
        out = ("{ОбщийМодуль.Общий.Модуль(5,5)}: Ожидается выражение\r\n\tА =<<?>> ; (Проверка: Сервер)\r\n"
               + OUTPUT)
        res, p = self.check({"designer-check": (101, 5.0, out)})
        self.assertEqual(p.calls, ["control-load", "designer-check", "control-restore"])
        self.assertEqual((res["control"]["variant"], res["control"]["ok"]), ("B", True))
        self.assertIn("ИБ контроля не создаётся", res["control"]["detail"])
        self.assertEqual(res["code"], 1)
        self.assertNotIn("ОбщийМодуль.Общий.Модуль", [f["object"] for f in res["findings"]])

    def test_inline_only_control_error_is_green(self):
        out = "{ОбщийМодуль.Общий.Модуль(5,5)}: Ожидается выражение\r\n"
        res, _ = self.check({"designer-check": (101, 5.0, out)})
        self.assertEqual((res["code"], res["findings"]), (0, []))

    def test_inline_designer_silent(self):
        res, p = self.check({"designer-check": (0, 5.0, "Синтаксических ошибок не обнаружено!")})
        self.assertEqual(res["code"], 2)
        self.assertIn("проверка Конфигуратором не работает", res["notes"][-1])
        self.assertEqual(p.calls[-1], "control-restore")                  # модуль восстанавливается всегда

    def test_inline_other_error_in_module(self):
        """Платформа показывает первую синтаксическую ошибку модуля — контрольная скрыта настоящей."""
        res, _ = self.check({"designer-check": (101, 5.0, "{ОбщийМодуль.Общий.Модуль(1,1)}: Ожидается выражение\r\n")})
        self.assertEqual(res["code"], 2)
        self.assertIn("в модуле есть другая ошибка", res["notes"][-1])

    def test_inline_support_disabled(self):
        loads = iter([(1, 1.0, "Загрузка невозможна: редактирование объекта метаданных CommonModule.Общий запрещено!"),
                      (0, 1.0, "")])

        class P(Platform2):
            def designer(self, *args, label="", timeout=0):
                if label == "control-load":
                    self.calls.append(label)
                    return next(loads)
                return super().designer(*args, label=label, timeout=timeout)
        p = P({"designer-check": (101, 5.0, "{ОбщийМодуль.Общий.Модуль(5,5)}: Ожидается выражение\r\n")})
        p.ib = self.tmp / "ib"
        res = designer_check.check(p, self.dump, "src", ["-Server"], self.tmp / "b.json", self.tmp / "run",
                                   log=lambda m: None)
        self.assertEqual(p.calls, ["control-load", "disable-support", "control-load", "designer-check",
                                   "control-restore"])
        self.assertEqual(res["code"], 0)

    def test_inline_not_in_working_base(self):
        p = Platform2({})
        p.connection = "/Sсервер/база"
        res = designer_check.check(p, self.dump, "src", ["-Server"], self.tmp / "b.json", self.tmp / "run",
                                   log=lambda m: None)
        self.assertEqual((res["code"], p.calls), (2, []))
        self.assertIn("не в рабочей базе", res["notes"][-1])

    def test_rc_without_parsed_is_infra(self):
        with mock.patch.object(designer_check, "minimal_control", return_value=(True, "ок")):
            res, _ = self.check({"designer-check": (1, 5.0, "что-то")})
        self.assertEqual(res["code"], 2)
        self.assertIn("без разобранных ошибок", res["notes"][-1])

    def test_modes_validated(self):
        cfg = {"environment": {}, "designerCheck": {"modes": ["-server", "-ExtendedModulesCheck"]}}
        self.assertEqual(designer_check.modes_of(cfg, None), ["-Server", "-ExtendedModulesCheck"])
        cfg = {"environment": {}, "designerCheck": {"modes": "-Server -MobileAppServer"}}
        self.assertEqual(designer_check.modes_of(cfg, None), ["-Server", "-MobileAppServer"])
        for bad, why in ((["-Sever"], "неизвестные ключи"), ([], "нет ни одного режима"),
                         (["-ExtendedModulesCheck"], "нет ни одного режима"), (["-Server", "-AllExtensions"], "неизв")):
            with self.assertRaisesRegex(designer_check.InfraError, why):
                designer_check.modes_of({"environment": {}, "designerCheck": {"modes": bad}}, None)

    def test_run_reports_broken_check(self):
        from otk_1c.probes import designer_check as fc
        orig_build, orig_platform, orig_rev = fc.build_mod.build, fc.Platform, fc.gitdiff.rev
        fc.build_mod.build = lambda *a, **k: {"action": "up-to-date"}
        fc.Platform = lambda env, run_dir: FakePlatform((0, 1.0, ""))
        fc.gitdiff.rev = lambda repo, ref: "f" * 40
        try:
            cfg = {"environment": {}, "project": {"source": "src"}}
            s = fc.run(self.tmp, cfg, self.tmp / "run", log=lambda m: None)
        finally:
            fc.build_mod.build, fc.Platform, fc.gitdiff.rev = orig_build, orig_platform, orig_rev
        self.assertEqual(s["code"], 2)
        self.assertIn("проверка Конфигуратором не работает", fc.markdown(s))
        self.assertTrue((self.tmp / "run" / "designer-check.md").exists())


class CiTest(unittest.TestCase):
    def test_env_settings(self):
        """Переменные OTK_* — поверх otk.local.toml, в память: файлы checkout не пишутся, пароль на диск не попадает."""
        tmp = Path(tempfile.mkdtemp())
        try:
            local = '[environment]\nib = "C:/local/ib"\nplatform = "C:/1cv8"\n'
            (tmp / "otk.local.toml").write_text(local, encoding="utf-8")
            environ = {"OTK_IB": r"C:\ci\ib\demo", "OTK_USER": "Администратор", "OTK_PASSWORD": "секрет",
                       "OTK_YAXUNIT": ""}
            cfg = config.load(tmp, environ)
            self.assertEqual(sorted(config.env_values(environ)), ["ib", "password", "user"])
            env = cfg["environment"]
            self.assertEqual((env["ib"], env["user"], env["password"], env["platform"]),
                             (r"C:\ci\ib\demo", "Администратор", "секрет", "C:/1cv8"))
            self.assertEqual((tmp / "otk.local.toml").read_text(encoding="utf-8"), local)
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), ["otk.local.toml"])
            empty = tmp / "пусто"
            empty.mkdir()
            self.assertEqual(config.load(empty, {"OTK_PASSWORD": "x"})["environment"]["password"], "x")
            cfg = config.load(empty, {})
            self.assertFalse(cfg["environment"].get("password"))     # DEFAULTS не испорчены прошлым вызовом
        finally:
            rmtree_force(tmp)


class GateDesignerTest(unittest.TestCase):
    """Шаг designer gate — контроль и проверка в той же копии ИБ, designer.json с отпечатком плана."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cm = self.tmp / "src" / "CommonModules"
        (cm / "Общий" / "Ext").mkdir(parents=True)
        (cm / "Общий" / "Ext" / "Module.bsl").write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8")
        (cm / "Общий.xml").write_text(FLAGS.format(g="false", c="false", s="true"), encoding="utf-8")
        self.plan = {"source": "src", "headSha": None, "modules": []}
        self.cfg = {"environment": {"ib": str(self.tmp / "ib")}, "project": {"source": "src"},
                    "designerCheck": {"modes": ["-Server"]}}
        self.minimal = mock.patch.object(designer_check, "minimal_control", return_value=(True, "ИБ контроля — ок"))
        self.minimal.start()

    def tearDown(self):
        self.minimal.stop()
        rmtree_force(self.tmp)

    def step(self, platform, built=True, cfg=None):
        from otk_1c.gate import pipeline
        from otk_1c.sources import workspace
        orig = pipeline.Platform, workspace.tree_root
        pipeline.Platform = lambda env, run_dir: platform
        workspace.tree_root = lambda repo, plan, run_dir: self.tmp
        try:
            return pipeline.designer_step(self.tmp, cfg or self.cfg, self.tmp / "run", self.plan, built,
                                          log=lambda m: None)
        finally:
            pipeline.Platform, workspace.tree_root = orig

    def test_ran_and_written(self):
        control = "{ОбщийМодуль.Общий.Модуль(5,5)}: Ожидается выражение\r\n"
        p = Platform2({"control-check": (101, 1.0, control), "designer-check": (101, 2.0, OUTPUT)})
        (self.tmp / "run").mkdir()
        d = self.step(p)
        self.assertTrue(d["ran"])
        self.assertEqual(d["modes"], ["-Server"])
        self.assertEqual(len(d["findings"]), 7)
        self.assertEqual(d["findings"][0]["file"], "src/CommonModules/КассовыеСмены/Ext/Module.bsl")
        saved = json.loads((self.tmp / "run" / "designer.json").read_text(encoding="utf-8"))
        from otk_1c import model
        self.assertEqual(saved["plan"], model.plan_id(self.plan))
        import schema_check
        self.assertEqual(schema_check.errors(saved, schema_check.load("designer")), [])
        self.assertNotIn("build", p.calls)

    def test_control_failed_is_infra(self):
        (self.tmp / "run").mkdir()
        p = Platform2({"designer-check": (0, 1.0, "")})
        p.ib = self.tmp / "ib"
        with mock.patch.object(designer_check, "minimal_control", return_value=(False, "НЕ найдена")):
            d = self.step(p)
        self.assertFalse(d["ran"])
        self.assertIn("проверка Конфигуратором не работает", d["infra"][0]["message"])

    def test_rc_without_parsed_is_infra(self):
        control = "{ОбщийМодуль.Общий.Модуль(5,5)}: Ожидается выражение\r\n"
        (self.tmp / "run").mkdir()
        d = self.step(Platform2({"control-check": (101, 1.0, control), "designer-check": (1, 1.0, "что-то")}))
        self.assertFalse(d["ran"])
        self.assertIn("без разобранных ошибок", d["infra"][0]["message"])

    def test_working_base_and_no_ib(self):
        (self.tmp / "run").mkdir()
        d = self.step(Platform2({}), cfg={**self.cfg, "environment": {"connection": "Srvr=x"}})
        self.assertIn("не в рабочей базе", d["infra"][0]["message"])
        d = self.step(Platform2({}), cfg={**self.cfg, "environment": {}})
        self.assertIn("нет копии ИБ", d["infra"][0]["message"])


class Platform2(ConfigOps):
    """Конфигуратор-заглушка: ответы по метке вызова, остальное — rc=0."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def designer(self, *args, label="", timeout=0):
        self.calls.append(label)
        return self.answers.get(label, (0, 0.1, ""))


class MiniPlatform(Platform2):
    """Заглушка для ИБ контроля: создание файловой ИБ, выгрузка пустой конфигурации; протокол (метка, ИБ,
    пользователь, аргументы) общий у копий (designer_check._on)."""
    def __init__(self, answers):
        super().__init__(answers)
        self.bin = Path("C:/Program Files/1cv8/8.3.27.2342/bin")
        self.connection, self.user, self.password, self.args, self.create_rc = None, "Админ", "", [], 0

    def create_file_ib(self, path, timeout=0):
        if self.create_rc:
            return self.create_rc, "нет лицензии"
        Path(path).mkdir(parents=True)
        return 0, ""

    def designer(self, *args, label="", timeout=0):
        self.args.append((label, self.ib, self.user, args))
        if label == "control-ib-dump":                     # выгрузка пустой конфигурации
            dump = Path(args[1])
            (dump / "Languages").mkdir(parents=True)
            (dump / "Configuration.xml").write_text(
                "<Configuration><Properties><DefaultLanguage>Language.Русский</DefaultLanguage>"
                "<InterfaceCompatibilityMode>Taxi</InterfaceCompatibilityMode></Properties>"
                "<ChildObjects><Language>Русский</Language></ChildObjects></Configuration>", encoding="utf-8-sig")
            (dump / "Languages" / "Русский.xml").write_text(
                '<Language uuid="u1"><LanguageCode>ru</LanguageCode></Language>', encoding="utf-8-sig")
        return super().designer(*args, label=label, timeout=timeout)


if __name__ == "__main__":
    unittest.main()
