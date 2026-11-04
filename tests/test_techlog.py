"""Анализ ТЖ на синтетическом журнале: многострочные события, кавычки, нормализация, отчёты, 8.2/8.3."""
import os
import tempfile
import unittest
from datetime import datetime

from _fs import rmtree_force
from otk_1c.extras import techlog

RPHOST = """﻿00:01.000001-2500000,CALL,1,process=rphost,p:processName=demo,t:clientID=5,Usr=Иванов,\
Context='Форма.Вызов : Документ.Заказ.Форма.ФормаДокумента.Модуль.ЗаписатьНаСервере
Документ.Заказ.МодульОбъекта : 10 : Записать()',Memory=1048576,MemoryPeak=2097152,InBytes=100,OutBytes=200
00:02.000002-500000,CALL,1,process=rphost,\
Context='Форма.Вызов : Документ.Заказ.Форма.ФормаДокумента.Модуль.ЗаписатьНаСервере
Документ.Заказ.МодульОбъекта : 10 : Записать()',Memory=10,MemoryPeak=20,InBytes=1,OutBytes=2
00:03.000003-120000,DBMSSQL,4,process=rphost,Trans=1,dbpid=55,\
Sql='SELECT T1._IDRRef FROM dbo._Reference10 T1 WHERE T1._IDRRef = ? AND T1._Fld12 = \
4216b7c0-febd-f741-82bc-eff80930a8ad
p_0: 0x0123
',Rows=1,Context='ОбщийМодуль.Цены.Модуль : 5 : Запрос.Выполнить()'
00:04.000004-80000,DBMSSQL,4,process=rphost,\
Sql='SELECT T1._IDRRef FROM dbo._Reference10 T1 WHERE T1._IDRRef = ? AND T1._Fld12 = \
51ae237d-ae58-d1d6-c94b-4e42c1ab63e0
p_0: 0x0456
',Rows=1,Context='ОбщийМодуль.Цены.Модуль : 5 : Запрос.Выполнить()'
00:05.000005-30000,DBMSSQL,4,process=rphost,Sql='INSERT INTO #tt12 (_Q_000_F_000) SELECT 1',Rows=1
00:06.000006-0,EXCP,3,process=rphost,Exception=DataBaseException,\
Descr='Конфликт блокировок при выполнении транзакции: (идентификатор процесса 7764) ''Тест''',\
Context='ОбщийМодуль.Склад.Модуль : 20 : НЗ.Записать();'
00:07.000007-0,EXCP,3,process=rphost,\
Descr='Конфликт блокировок при выполнении транзакции: (идентификатор процесса 8812) ''Тест''',\
Context='ОбщийМодуль.Склад.Модуль : 20 : НЗ.Записать();'
00:08.000008-15000000,TLOCK,5,process=rphost,Regions=InfoRg404.DIMS,\
Locks='InfoRg404.DIMS Exclusive Fld405=4216B7C0FEBDF74182BCEFF80930A8AD',WaitConnections=16,\
Context='ОбщийМодуль.Склад.Модуль : 20 : НЗ.Записать();'
00:09.000009-10,TLOCK,5,process=rphost,Regions=InfoRg404.DIMS,Locks='',WaitConnections=,Context='без ожидания'
00:10.000010-20000000,TTIMEOUT,5,process=rphost,WaitConnections=16,\
Context='ОбщийМодуль.Склад.Модуль : 20 : НЗ.Записать();'
00:12.000012-4000,DBV8DBEng,4,process=rphost,Sql='SELECT F1 FROM T1',Context='Файловая база'
00:13.000013-0,QERR,4,process=rphost,Descr='{(1, 11)}: Поле не найдено "Т.Нет"',\
Query='ВЫБРАТЬ Т.Нет ИЗ Справочник.Т КАК Т'
00:11.000011-100,SDBL,4,process=rphost,Trans=1,Sdbl='SELECT Т.Ссылка FROM Справочник.Номенклатура Т',\
Query='ВЫБРАТЬ Т.Ссылка ИЗ Справочник.Номенклатура КАК Т'
"""
OLD82 = "30:00.0010-25000,CALL,1,process=rphost,Context='Старый контекст 8.2'\n"


class TestTechlog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="otk-tj-")
        for proc, name, text in (("rphost_1234", "26100811.log", RPHOST), ("rphost_99", "26100812.log", OLD82)):
            os.makedirs(os.path.join(cls.tmp, proc))
            with open(os.path.join(cls.tmp, proc, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def run_report(self, kind, **kw):
        return techlog.report([self.tmp], kind, **kw)

    def test_props_quotes_and_multiline(self):
        p = techlog.parse_props("a=1,Descr='x, ''y''',Context='строка1\nстрока2',b=")
        self.assertEqual(p, {"a": "1", "Descr": "x, 'y'", "Context": "строка1\nстрока2", "b": ""})

    def test_events_time_and_duration(self):
        evs = list(techlog.iter_events([self.tmp]))
        first = evs[0]
        self.assertEqual((first["event"], first["duration"], first["time"]),
                         ("CALL", 2500000, datetime(2026, 10, 8, 11, 0, 1)))
        old = [e for e in evs if e["process"] == "rphost_99"][0]
        self.assertEqual(old["duration"], 2500000)            # 8.2: 25000 × 1/10000 с = 2,5 с

    def test_calls_by_context_and_memory(self):
        r = self.run_report("calls")
        self.assertEqual(r["rows"][0]["count"], 2)
        self.assertEqual(r["rows"][0]["sum"], 3000000)
        self.assertEqual(r["rows"][0]["max"], 2500000)
        m = self.run_report("calls", value="memory", sort="max")
        self.assertEqual(m["rows"][0]["max"], 1048576)

    def test_queries_normalized(self):
        r = self.run_report("queries")                      # GUID и параметры p_N убраны — один запрос
        self.assertEqual(r["rows"][0]["count"], 2)
        self.assertIn("{GUID}", r["rows"][0]["key"])
        self.assertNotIn("p_0", r["rows"][0]["key"])
        self.assertTrue(any("{TempTable}" in x["key"] for x in r["rows"]))
        ctx = self.run_report("queries", mode="context")
        self.assertEqual(ctx["rows"][0]["key"], "ОбщийМодуль.Цены.Модуль : 5 : Запрос.Выполнить()")
        both = self.run_report("queries", mode="sql+context")
        self.assertIn("--- Context:", both["rows"][0]["key"])

    def test_errors_and_locks(self):
        e = self.run_report("errors", sort="count")
        self.assertEqual(e["rows"][0]["count"], 2)          # идентификаторы процессов убраны
        self.assertNotIn("идентификатор процесса", e["rows"][0]["key"])
        lk = self.run_report("locks")
        self.assertEqual((lk["events"], lk["rows"][0]["sum"]), (1, 15000000))   # без WaitConnections — не ожидание
        self.assertTrue(lk["rows"][0]["key"].startswith("InfoRg404.DIMS"))
        to = self.run_report("timeouts")
        self.assertEqual(to["rows"][0]["count"], 1)

    def test_events_summary_and_filters(self):
        r = self.run_report("events")
        by = {x["key"]: x for x in r["rows"]}
        self.assertEqual(by["CALL"]["count"], 3)
        self.assertEqual(by["DBMSSQL"]["sum"], 230000)
        only = techlog.report([self.tmp], "calls", process="rphost_99")
        self.assertEqual(only["events"], 1)
        late = techlog.report([self.tmp], "calls", since=datetime(2026, 10, 8, 12, 0))
        self.assertEqual(late["events"], 1)
        self.assertIn("=sum", techlog.format_report(r))

    def test_chunks_same_result(self):
        path = os.path.join(self.tmp, "rphost_1234", "26100811.log")
        parts = techlog.chunks(path, size=200)                  # куски по границам событий
        self.assertGreater(len(parts), 3)
        whole = [e["event"] for e in techlog.iter_events([path])]
        pieces = [e["event"] for a, b in parts for e in techlog.iter_events([path], rng=(a, b))]
        self.assertEqual(whole, pieces)
        orig = techlog._CHUNK
        try:
            techlog._CHUNK = 200
            chunked = techlog.report([self.tmp], "queries", workers=1)
        finally:
            techlog._CHUNK = orig
        self.assertEqual(chunked["rows"], techlog.report([self.tmp], "queries", workers=1)["rows"])

    def test_file_base_and_query_errors(self):
        r = self.run_report("queries", mode="context")         # DBV8DBEng — смешанный регистр в файловой базе
        self.assertIn("Файловая база", [x["key"] for x in r["rows"]])
        e = self.run_report("errors")                            # QERR — ошибки запросов, по количеству
        self.assertIn('{(1, 11)}: Поле не найдено "Т.Нет"', [x["key"] for x in e["rows"]])
        self.assertEqual(e["rows"][0]["count"], 2)               # сортировка по количеству по умолчанию

    def test_bad_report(self):
        with self.assertRaises(ValueError):
            techlog.report([self.tmp], "нет")


if __name__ == "__main__":
    unittest.main()
