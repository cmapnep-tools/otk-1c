"""MCP-сервер консоли ИБ (stdio, JSON-RPC построчно; только стандартная библиотека + pywin32 для COM).

    otk mcp [--idle 120]

Вызовы COM идут из главного потока (STA); чтение stdin — в отдельном потоке, чтобы по простою
отпускать соединения с базами без запроса от клиента.
"""
import json
import queue
import sys
import threading
from datetime import datetime

from . import console, techlog

NAME, VERSION = "otk-console", "1.0.0"
_BASE = {"type": "string", "description": "Псевдоним базы из файла баз (ib_bases) или «/Fкаталог» / «/Sсервер\\база»"}
TOOLS = [
    {"name": "ib_bases", "description": "Базы, описанные в файле баз пользователя, и состояние соединений.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ib_query",
     "description": "Выполнить запрос (или пакет через «;») в копии ИБ, только чтение. Ответ — результаты пакета: "
                    "колонки, строки (до limit), всего строк. Ссылка — {repr, type, id}; type+id годятся в параметры.",
     "inputSchema": {"type": "object", "properties": {
         "base": _BASE, "text": {"type": "string", "description": "Текст запроса на языке запросов 1С"},
         "params": {"type": "object", "description": "Параметры: строка/число/булево/null, {date}, "
                                                     "{type, id} для ссылки или значения перечисления, список"},
         "limit": {"type": "integer", "description": "Строк на результат (по умолчанию 200, максимум 5000)"}},
         "required": ["base", "text"]}},
    {"name": "ib_check", "description": "Разобрать запрос платформой без выполнения: ошибки с позицией и параметры.",
     "inputSchema": {"type": "object", "properties": {"base": _BASE, "text": {"type": "string"}},
                     "required": ["base", "text"]}},
    {"name": "ib_metadata",
     "description": "Метаданные живой базы: коллекция («Справочники») — список объектов; полное имя "
                    "(«Справочник.Номенклатура», «РегистрНакопления.Остатки») — реквизиты, ТЧ, измерения, "
                    "ресурсы с типами.",
     "inputSchema": {"type": "object", "properties": {"base": _BASE, "name": {"type": "string"}},
                     "required": ["base", "name"]}},
    {"name": "ib_eventlog",
     "description": "Журнал регистрации копии ИБ (только чтение): кто, когда, что менял/проводил, ошибки. Выгрузка "
                    "идёт от начала периода вперёд — задавайте from. Без group_by — записи (до limit); с group_by — "
                    "подсчёт по полям среди первых scan записей (кто/что/сколько). Системные события: "
                    "_$Data$_.New|Update|Delete|Post|Unpost, "
                    "_$Session$_.Start|Finish|Authentication|AuthenticationError, "
                    "_$PerformError$_, _$Transaction$_.Begin|Commit|Rollback, _$InfoBase$_.ConfigUpdate; события "
                    "конфигурации — по их имени.",
     "inputSchema": {"type": "object", "properties": {
         "base": _BASE,
         "filter": {"type": "object", "description":
                    "from/to (ISO-дата), level (Информация|Предупреждение|Ошибка|Примечание, можно списком), "
                    "user (имя), "
                    "event (имя события, список), metadata (полное имя объекта, список), data ({type, id} — ссылка из "
                    "ib_query), dataPresentation, comment, session, application, computer, "
                    "transactionStatus (Зафиксирована|Отменена|НеЗавершена|НетТранзакции)"},
         "group_by": {"type": "array", "items": {"type": "string"}, "description":
                      "user | event | metadata | level | application | computer | session | status | "
                      "date:день | date:час"},
         "limit": {"type": "integer", "description": "Записей или групп (по умолчанию 200, максимум 5000)"},
         "scan": {"type": "integer",
                  "description": "Сколько записей просматривать при group_by (по умолчанию 100000)"}},
         "required": ["base"]}},
    {"name": "tj_report",
     "description": "Анализ технологического журнала 1С на диске (каталоги процессов rphost_*/ГГММДДЧЧ.log): агрегаты "
                    "сумма/количество/среднее/максимум по группам. Отчёты: calls — серверные вызовы по контексту "
                    "(value: duration|memory|memorypeak|inbytes|outbytes|cpu); queries — запросы СУБД, sdbl — запросы "
                    "платформы (mode: sql|sql+context|context); errors — EXCP и ошибки запросов QERR по описанию, "
                    "по количеству (mode: descr|descr+context); "
                    "locks — ожидания TLOCK с WaitConnections по пространству и контексту; "
                    "timeouts — TTIMEOUT/TDEADLOCK; "
                    "events — сводка по типам событий. Длительности — в микросекундах. Без базы и сети.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Каталог ТЖ или файл .log (несколько — через ;)"},
         "report": {"type": "string", "enum": ["calls", "queries", "sdbl", "errors", "locks", "timeouts", "events"]},
         "mode": {"type": "string"}, "value": {"type": "string"},
         "sort": {"type": "string", "enum": ["sum", "count", "mean", "max"]},
         "top": {"type": "integer", "description": "По умолчанию 30"},
         "process": {"type": "string", "description": "Только процессы с этим началом имени (rphost)"},
         "from": {"type": "string"}, "to": {"type": "string"}},
         "required": ["path", "report"]}},
    {"name": "ib_disconnect", "description": "Отпустить соединение с базой (все — без base): перед загрузкой "
                                             "расширения или работой Конфигуратора с этой базой.",
     "inputSchema": {"type": "object", "properties": {"base": _BASE}}},
]


def call(con, name, a):
    if name == "ib_bases":
        bases = console.load_bases() if con.bases is None else con.bases
        return {"file": str(console.bases_file()),
                "bases": {k: {"connection": v.get("connection"), "user": v.get("user", ""),
                              "connected": k in con.sessions and con.sessions[k].conn is not None}
                          for k, v in bases.items()}}
    if name == "tj_report":

        try:
            res = techlog.report([p for p in a["path"].split(";") if p], a["report"], a.get("mode"),
                                 a.get("value") or "duration", a.get("sort") or None,
                                 max(1, min(int(a.get("top") or 30), 500)), a.get("process"),
                                 datetime.fromisoformat(a["from"]) if a.get("from") else None,
                                 datetime.fromisoformat(a["to"]) if a.get("to") else None)
        except ValueError as e:
            raise console.ConsoleError(str(e)) from None
        for r in res["rows"]:
            if len(r["key"]) > 4000:
                r["key"] = r["key"][:4000] + "…"
        return res
    if name == "ib_disconnect":
        con.close(a.get("base"))
        return {"ok": True}
    s = con.session(a["base"])
    if name == "ib_query":
        return s.query(a["text"], a.get("params"), max(1, min(int(a.get("limit") or 200), 5000)))
    if name == "ib_check":
        return s.check(a["text"])
    if name == "ib_metadata":
        return s.metadata(a["name"])
    if name == "ib_eventlog":
        return s.eventlog(a.get("filter"), max(1, min(int(a.get("limit") or 200), 5000)), a.get("group_by") or None,
                          max(1, min(int(a.get("scan") or 100000), 1000000)))
    raise console.ConsoleError(f"неизвестный инструмент: {name}")


def dispatch(con, body):
    method, rid, params = body.get("method"), body.get("id"), body.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": NAME, "version": VERSION},
            "instructions": "Консоль копии ИБ 1С: запросы (только чтение), разбор запроса, метаданные, "
                            "журнал регистрации; "
                            "анализ технологического журнала на диске (tj_report). "
                            "Соединение держится между вызовами и отпускается по простою; перед загрузкой "
                            "расширения в ту же базу — ib_disconnect."}}
    if rid is None:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            res, err = call(con, params.get("name"), params.get("arguments") or {}), False
        except console.ConsoleError as e:
            res, err = {"error": str(e)}, True
        except Exception as e:                       # noqa: BLE001 — сбой не должен ронять сервер
            res, err = {"error": f"{type(e).__name__}: {e}"}, True
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}], "isError": err}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"метод не найден: {method}"}}


def serve(idle=120, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for stream in (stdin, stdout):
        try:
            if stream is stdout:
                stream.reconfigure(encoding="utf-8", newline="\n")
            else:
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    lines = queue.Queue()

    def reader():
        for line in stdin:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    con = console.Console(idle=idle)
    try:
        while True:
            try:
                line = lines.get(timeout=min(15, idle))
            except queue.Empty:
                con.release_idle()
                continue
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                out = dispatch(con, json.loads(line))
            except json.JSONDecodeError as e:
                out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}}
            if out is not None:
                stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
                stdout.flush()
            con.release_idle()
    finally:
        con.close()
