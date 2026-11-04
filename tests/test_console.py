"""Консоль ИБ без 1С: строки соединения, базы, значения, ошибки запросов, MCP на подставном соединении."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from otk_1c.extras import console, mcp


class Com:
    """Подставной COM-объект: признак _oleobj_ и произвольные атрибуты."""
    _oleobj_ = True

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeConn:
    def String(self, v):
        return v.repr

    def XMLТипЗнч(self, v):
        return Com(ИмяТипа=v.xtype)

    def XMLСтрока(self, v):
        return v.id

    def ИзXMLТипа(self, name, ns):
        return name

    def XMLЗначение(self, t, s):
        return ("ref", t, s)

    def NewObject(self, name, *args):
        if name == "Массив":
            return FakeArray()
        return FakeQuery(args[0] if args else "")


class FakeArray(list):
    def Добавить(self, v):
        self.append(v)


class Coll:
    def __init__(self, items):
        self.items = items

    def Количество(self):
        return len(self.items)

    def Получить(self, i):
        return self.items[i]


class FakeQuery:
    def __init__(self, text):
        self.text, self.params = text, {}

    def УстановитьПараметр(self, k, v):
        self.params[k] = v

    def ВыполнитьПакет(self):
        ref = Com(repr="Массаж век", xtype="CatalogRef.Номенклатура", id="a023")
        rows = [Coll([ref, 1.5, None, datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)]) for _ in range(3)]
        tab = Coll(rows)
        tab.Колонки = Coll([Com(Имя=n) for n in ("Ссылка", "Число", "Пусто", "Дата")])
        return Coll([Com(Выгрузить=lambda: tab)])


class TestConsole(unittest.TestCase):
    def test_connection_string(self):
        self.assertEqual(console.connection_string("/FC:/1C/Демо", "Админ"), 'File="C:/1C/Демо";Usr="Админ";')
        self.assertEqual(console.connection_string("/Ssrv\\demo", "А", "п"), 'Srvr="srv";Ref="demo";Usr="А";Pwd="п";')
        self.assertEqual(console.connection_string("/Ssrv/demo"), 'Srvr="srv";Ref="demo";')

    def test_bases_and_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bases.toml")
            with open(p, "w", encoding="utf-8") as f:
                f.write('[bases.demo]\nconnection = "/FC:/1C/Демо"\nuser = "А"\npassword_env = "KV_TEST_PWD"\n')
            bases = console.load_bases(p)
            with mock.patch.dict(os.environ, {"KV_TEST_PWD": "секрет"}):
                b = console.resolve("demo", bases)
            self.assertEqual((b["user"], b["password"]), ("А", "секрет"))
            self.assertEqual(console.resolve("/FC:/x", bases)["connection"], "/FC:/x")
            with self.assertRaises(console.ConsoleError):
                console.resolve("нет", bases)

    def test_values(self):
        conn = FakeConn()
        ref = Com(repr="Р", xtype="CatalogRef.Т", id="1")
        self.assertEqual(console.to_py(conn, ref), {"repr": "Р", "type": "CatalogRef.Т", "id": "1"})
        self.assertEqual(console.to_py(conn, datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)),
                         "2026-01-02T00:00:00")
        self.assertEqual(console.to_1c(conn, {"type": "CatalogRef.Т", "id": "1"}), ("ref", "CatalogRef.Т", "1"))
        d = console.to_1c(conn, {"date": "2026-01-31T23:59:59"})
        self.assertEqual((d.day, d.hour, d.tzinfo), (31, 23, datetime.timezone.utc))
        self.assertEqual(console.to_1c(conn, [1, {"type": "EnumRef.В", "id": "Опт"}]), [1, ("ref", "EnumRef.В", "Опт")])

    def test_query_error(self):
        e = console.parse_query_error('{(1, 11)}: Поле не найдено "Т.Нет" ВЫБРАТЬ Т.<<?>>Нет ИЗ Справочник.Н КАК Т')
        self.assertEqual(e, {"line": 1, "col": 11, "message": 'Поле не найдено "Т.Нет"'})
        self.assertEqual(console.parse_query_error("что-то иное")["message"], "что-то иное")

    def test_query_via_mcp(self):
        con = console.Console(idle=60, bases={"t": {"connection": "/FC:/x"}})
        s = con.session("t")
        s.conn, s.last = FakeConn(), 0
        with mock.patch.object(console.Session, "connect", lambda self: self.conn):
            out = mcp.dispatch(con, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "ib_query", "arguments": {"base": "t", "text": "ВЫБРАТЬ", "limit": 2}}})
        res = json.loads(out["result"]["content"][0]["text"])
        r = res["results"][0]
        self.assertEqual(r["columns"], ["Ссылка", "Число", "Пусто", "Дата"])
        self.assertEqual((r["total"], r["truncated"], len(r["rows"])), (3, True, 2))
        self.assertEqual(r["rows"][0][0]["type"], "CatalogRef.Номенклатура")

    def test_idle_release(self):
        con = console.Console(idle=10, bases={"t": {"connection": "/FC:/x"}})
        s = con.session("t")
        s.conn, s.last = object(), 0                    # давно не пользовались
        con.release_idle()
        self.assertIsNone(s.conn)

    def test_mcp_errors_do_not_crash(self):
        con = console.Console(bases={})
        out = mcp.dispatch(con, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "ib_query", "arguments": {"base": "нет", "text": "ВЫБРАТЬ 1"}}})
        self.assertTrue(out["result"]["isError"])
        self.assertIn("не описана", out["result"]["content"][0]["text"])
        tools_list = mcp.dispatch(con, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(tools_list), 7)


if __name__ == "__main__":
    unittest.main()


class FakeLogConn(FakeConn):
    """Журнал регистрации: две записи, ВыгрузитьЖурналРегистрации заполняет таблицу."""

    def __init__(self):
        self.filter = None
        lv = type("Lv", (), {"Ошибка": Com(repr="Ошибка", xtype="x", id="")})()
        self.УровеньЖурналаРегистрации = lv
        self.Метаданные = Com(НайтиПоПолномуИмени=lambda n: (Com(repr=n, xtype="", id="")
                                                             if n.startswith("Документ.") else None))

    def NewObject(self, name, *args):
        if name == "Структура":
            st = {}
            return Com(Вставить=lambda k, v: st.__setitem__(k, v), data=st)
        if name == "ТаблицаЗначений":
            return Coll([])
        return super().NewObject(name, *args)

    def String(self, v):
        return getattr(v, "repr", str(v))

    def ВыгрузитьЖурналРегистрации(self, tab, flt, cols, _file, limit):
        self.filter, self.limit = flt.data, limit
        names = ["Дата", "Уровень", "ИмяПользователя", "ПредставлениеСобытия", "Метаданные"]
        tab.Колонки = Coll([Com(Имя=n) for n in names])
        d = datetime.datetime(2026, 10, 8, 12, tzinfo=datetime.timezone.utc)
        tab.items = [Coll([d, "Информация", "Иванов", "Данные. Проведение", "Документ.Заказ"]),
                     Coll([d, "Информация", "Иванов", "Данные. Проведение", "Документ.Заказ"]),
                     Coll([d, "Ошибка", "Петров", "Ошибка выполнения", ""])]


class TestEventLog(unittest.TestCase):
    def session(self):
        con = console.Console(bases={"t": {"connection": "/FC:/x"}})
        s = con.session("t")
        s.conn, s.last = FakeLogConn(), 0
        return s

    def test_rows_and_filter(self):
        s = self.session()
        with mock.patch.object(console.Session, "connect", lambda self: self.conn):
            r = s.eventlog({"from": "2026-10-01", "user": "Иванов", "metadata": "Документ.Заказ"}, limit=2)
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["rows"][0]["ИмяПользователя"], "Иванов")
        self.assertEqual(r["rows"][0]["Дата"], "2026-10-08T12:00:00")
        flt = s.conn.filter
        self.assertEqual(flt["ДатаНачала"].day, 1)
        self.assertEqual(list(flt["Пользователь"]), ["Иванов"])
        self.assertEqual(s.conn.limit, 2)

    def test_group_by(self):
        s = self.session()
        with mock.patch.object(console.Session, "connect", lambda self: self.conn):
            r = s.eventlog({"from": "2026-10-01"}, limit=10, group_by=["user", "date:день"], scan=1000)
        self.assertEqual(r["groups"][0], ["Иванов", "2026-10-08", 2])
        self.assertEqual((r["scanned"], r["groups_total"], s.conn.limit), (3, 2, 1000))

    def test_bad_filter(self):
        s = self.session()
        with mock.patch.object(console.Session, "connect", lambda self: self.conn):
            with self.assertRaises(console.ConsoleError):
                s.eventlog({"нет": 1})
            with self.assertRaises(console.ConsoleError):
                s.eventlog({"metadata": "Справочник.Нет"})
            with self.assertRaises(console.ConsoleError):
                s.eventlog({}, group_by=["нет"])
