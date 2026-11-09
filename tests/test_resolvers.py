"""Внешние восстановители собранных запросов: протокол команды, цепочка, сбой, кэш."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.gate import pipeline, report, resolvers

MODULE = ("Функция Остатки() Экспорт\n"
          "\tЗапрос = Новый Запрос(\"ВЫБРАТЬ Т.Ссылка ИЗ \" + ИмяТаблицы() + \" КАК Т\");\n"
          "\tВозврат Запрос.Выполнить();\n"
          "КонецФункции\n")
# восстановитель-заглушка: пишет полученное в файл, отвечает по режиму
FAKE = r'''
import json, sys
payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
log, mode = sys.argv[1], sys.argv[2]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
if mode == "fail":
    sys.stderr.buffer.write("нет ключа API\n".encode("utf-8")); sys.exit(1)
if mode == "garbage":
    print("не JSON"); sys.exit(0)
if mode == "sleep":
    import time; time.sleep(5)
qs = [] if mode == "none" else [{"id": q["id"], "text": q["text"].replace("<?>", "Справочник.Номенклатура"),
                                 "rationale": "ИмяТаблицы() в ОбщийМодуль.Склад возвращает литерал"}
                                for q in payload["queries"]]
sys.stdout.buffer.write(json.dumps({"queries": qs}, ensure_ascii=False).encode("utf-8"))
'''


def plan():
    return {"source": "src/cf", "head": "HEAD", "headSha": "x",
            "modules": [{"path": "CommonModules/М/Ext/Module.bsl", "owner": "ОбщийМодуль.М"}],
            "queries": [{"id": "q1", "module": "CommonModules/М/Ext/Module.bsl", "line": 2, "complete": False,
                         "text": "ВЫБРАТЬ Т.Ссылка ИЗ <?> КАК Т", "reason": "имя таблицы или поля собирается в коде"},
                        {"id": "q2", "module": "CommonModules/М/Ext/Module.bsl", "line": 2, "complete": True,
                         "text": "ВЫБРАТЬ 1"}]}


class TestResolvers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        self.script = self.tmp / "fake.py"
        self.script.write_text(FAKE, encoding="utf-8")
        self.log = self.tmp / "calls.jsonl"
        self.read = lambda rel: MODULE

    def tearDown(self):
        rmtree_force(self.tmp)

    def cfg(self, *modes, timeout=30):
        return {"queries": {"resolvers": [{"name": f"r{n}", "timeout": timeout,
                                           "command": [sys.executable, str(self.script), str(self.log), m]}
                                          for n, m in enumerate(modes, 1)]}}

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def resolve(self, p, cfg):
        return resolvers.resolve(p, self.tmp, self.read, self.tmp / "cache", cfg, log=lambda m: None)

    def test_command_without_shell(self):
        """Строка команды — аргументы, не текст для оболочки: подстановка оболочки не выполняется."""
        with mock.patch.object(resolvers.os, "name", "posix"):
            self.assertEqual(resolvers.argv('ask "a b" $(id); x'), ["ask", "a b", "$(id);", "x"])
        with mock.patch.object(resolvers.os, "name", "nt"):
            self.assertEqual(resolvers.argv("ask.exe --x"), "ask.exe --x")
        self.assertEqual(resolvers.argv(["ask", "--x"]), ["ask", "--x"])

    def test_not_configured(self):
        p = plan()
        self.assertEqual(self.resolve(p, {}), [])
        self.assertFalse(p["queries"][0]["complete"])
        self.assertEqual(self.calls(), [])

    def test_resolved_and_downgraded(self):
        p = plan()
        self.assertEqual(self.resolve(p, self.cfg("ok")), [])
        q = p["queries"][0]
        self.assertEqual((q["complete"], q["assembled"], q["resolver"]), (True, True, "r1"))
        self.assertIn("Справочник.Номенклатура", q["text"])
        self.assertNotIn("reason", q)
        (call,) = self.calls()
        self.assertEqual([x["id"] for x in call["queries"]], ["q1"])           # только неполные
        self.assertEqual((call["queries"][0]["method"], call["queries"][0]["owner"]), ("Остатки", "ОбщийМодуль.М"))
        self.assertEqual(call["modules"], {"CommonModules/М/Ext/Module.bsl": MODULE})
        self.assertEqual(call["version"], 1)
        why = report.query_downgrades({"queries": p["queries"]})["q1"]
        self.assertTrue(why.startswith("текст восстановлен «r1» (ИмяТаблицы()"))

    def test_failures_are_notes(self):
        for mode, text in (("fail", "код выхода 1 нет ключа API"), ("garbage", "ответ — не JSON"),
                           ("sleep", "нет ответа за 1 с")):
            with self.subTest(mode=mode):
                p = plan()
                (note,) = self.resolve(p, self.cfg(mode, timeout=1))
                self.assertIn(f"восстановитель «r1»: {text}", note)
                self.assertFalse(p["queries"][0]["complete"])
                rmtree_force(self.tmp / "cache")

    def test_chain(self):
        p = plan()
        self.resolve(p, self.cfg("none", "ok"))
        self.assertEqual(p["queries"][0]["resolver"], "r2")
        self.assertEqual(len(self.calls()), 2)

    def test_cache(self):
        self.resolve(plan(), self.cfg("none"))
        p = plan()
        self.resolve(p, self.cfg("none"))                  # «не восстановлен» тоже в кэше — второй раз не зовём
        self.assertEqual(len(self.calls()), 1)
        self.resolve(p, self.cfg("ok"))                    # другая команда — другой ключ
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(p["queries"][0]["complete"])

    def test_static_only(self):
        p = plan()
        notes = pipeline.resolve_queries(p, self.tmp, self.cfg("ok"), self.tmp, static_only=True)
        self.assertEqual(notes, ["внешние восстановители запросов не вызывались: --static-only"])
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
