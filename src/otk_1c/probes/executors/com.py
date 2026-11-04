"""Исполнитель COM (внешнее соединение): серверные модули, менеджеры, объекты, разбор запросов.

Одно соединение на все пробы (после ошибки компиляции соединение остаётся рабочим — спайк O6).
В файловой ИБ серверные модули компилируются в варианте «Сервер + ВнешнееСоединение»; модули с
ветками «НЕ ВнешнееСоединение» дополнительно пробуются на сервере через YAxUnit.
"""
import re

from ... import checks
from ..platform import WINDOWS, com_progid
from . import yaxunit
from .base import PROBE_METHOD, classify, compile_position, query_result, result

NAME = "com"


class Unavailable(Exception):
    """Соединение не установлено не из-за кода (версия коннектора не та, лицензия, вход): пробы — в обход
    через тонкий клиент (YAxUnit). Ошибка компиляции при подключении — находка, а не это."""


def available(progid="V85.COMConnector"):
    if not WINDOWS:
        return False, "COM недоступен: только Windows"
    try:
        import pythoncom  # noqa: F401
        import win32com.client
    except ImportError:
        return False, 'COM недоступен: нет pywin32 (pip install pywin32 или pip install "otk-1c[com]")'
    try:
        win32com.client.Dispatch(progid)
        return True, ""
    except Exception as e:  # noqa: BLE001 - коннектор не зарегистрирован или не грузится
        return False, f"COM недоступен ({progid}): {e}"


def _text(exc):
    info = getattr(exc, "excepinfo", None)
    return str(info[2] if info and info[2] else exc)


def suitable(entry):
    """Модуль пробуется через COM: серверный/внешнего соединения контекст и проба вызовом."""
    return (entry["probe"]["strategy"] in ("missing-method", "create-object")
            and any(c in ("Server", "ExternalConnection") for c in entry["contexts"]))


def run(platform, plan, modules, queries, progid="V85.COMConnector"):
    import pythoncom
    import win32com.client

    results = []
    platform.unlock()
    connector = win32com.client.Dispatch(progid)
    auth = f'Usr="{platform.user}";Pwd="{platform.password}";' if platform.user else ""
    try:
        target = platform.com_connection() if hasattr(platform, "com_connection") else f'File="{platform.ib}";'
        conn = connector.Connect(f'{target}{auth}')
    except pythoncom.com_error as e:
        text = _text(e)
        pos = compile_position(text)
        init = re.search(r"инициализации модуля:\s*([^\s|]+)", text)
        owner = pos[0] if pos else (init.group(1) if init else None)
        if not (pos or init):
            raise Unavailable(f"подключение не удалось ({progid}): {' '.join(text.split())[:300]}") from None
        out = [result(NAME, "session", {"path": None, "owner": owner} if owner else None, "ExternalConnection",
                      "compile-error" if (pos or init) else "unknown", " ".join(text.split())[:500],
                      pos[1] if pos else None)]
        out += [result(NAME, "probe", m, "ExternalConnection", "skipped", "не выполнено: COM-соединение не установлено")
                for m in modules]
        out += [{**result(NAME, "query", {"path": q["module"], "owner": q["id"]}, "Server", "skipped",
                          "не выполнено: COM-соединение не установлено"), "query": q["id"]} for q in queries]
        return out, f"подключение не удалось: {text[:300]}"

    for entry in modules:
        probe = entry["probe"]
        ctx = "ExternalConnection"
        try:
            obj = conn
            for part in probe["target"].split("."):
                obj = getattr(obj, part)
            if probe["strategy"] == "missing-method":
                getattr(obj, PROBE_METHOD)
                results.append(result(NAME, "probe", entry, ctx, "unknown", "метод-проба неожиданно найден"))
            else:
                getattr(obj, probe["method"])()
                results.append(result(NAME, "probe", entry, ctx, "ok"))
        except AttributeError as e:
            # GetIDsOfNames не нашёл: для пробы метода — ожидаемо (модуль скомпилирован),
            # для цели пробы — объекта нет (модуль недоступен во внешнем соединении)
            if PROBE_METHOD in str(e):
                results.append(result(NAME, "probe", entry, ctx, "ok"))
            else:
                results.append(result(NAME, "probe", entry, ctx, "skipped", f"недоступно через COM: {e}"))
        except pythoncom.com_error as e:
            outcome, line, message = classify(_text(e), entry)
            results.append(result(NAME, "probe", entry, ctx, outcome, message, line))

    for q in queries:
        entry = {"path": q["module"], "owner": q["id"]}
        try:
            conn.NewObject("Запрос", q["text"]).НайтиПараметры()
            results.append({**result(NAME, "query", entry, "Server", "ok"), "query": q["id"]})
        except pythoncom.com_error as e:
            results.append(query_result(NAME, q, "Server", _text(e)))
    del conn
    return results, ""


class Com:
    """Серверные пробы и запросы через внешнее соединение. Не подключилось не из-за кода (коннектор другой
    версии, лицензия) — в обход, клиентом: модули и запросы передаются исполнителю YAxUnit."""
    name = NAME
    checks = ("probes-server", "queries")
    fallback = yaxunit.NAME

    def select(self, ctx):
        modules, gate = ctx.modules, ctx.gate
        queries = [q for q in ctx.plan["queries"] if q["complete"]]
        # внешнее соединение убрано из environment.contexts — локально не проверяется: COM не запускается,
        # модули только внешнего соединения остаются с пометкой collect «не покрыто этим прогоном»
        ec_off = "ExternalConnection" not in ctx.plan["contextsAvailable"]
        if ec_off:
            modules = [m for m in modules if m["contexts"] != ["ExternalConnection"]]
        # модуль внешнего соединения компилируется при подключении COM — отдельной пробы у него нет
        session = [m for m in modules if m["kind"] == "ExternalConnectionModule"]
        server = [m for m in modules if suitable(m) or m.get("needsPlainServerProbe")] + session
        progid = ctx.env.get("comConnector") or com_progid(ctx.platform.bin)
        ok, note = False, "COM отключён настройкой (environment.executors.server)"
        if ec_off:
            note = "внешнее соединение исключено настройкой (environment.contexts)"
        elif ctx.env.get("executors", {}).get("server", "auto") != "yaxunit" and (
                (server and gate.enabled("probes-server")) or (queries and gate.enabled("queries"))):
            ok, note = available(progid)
        ctx.have["com"] = ok
        if ec_off and not ctx.have["yaxunit"]:
            # COM исключён настройкой, а не отсутствует — в причине «не проверено» совета про COM нет
            for name, wanted in (("probes-server", server), ("queries", queries)):
                if wanted and gate.enabled(name):
                    gate.failed(name, f"{note}; не найдено: {checks.DEPS['yaxunit']}", "no-yaxunit")
            return None
        run_server = bool(server) and gate.ready("probes-server", ctx.have)
        run_queries = bool(queries) and gate.ready("queries", ctx.have)
        plain = [m for m in modules if m.get("needsPlainServerProbe")] if run_server else []
        if ctx.have["yaxunit"]:
            ctx.hand(self.fallback, "server", plain)
        else:
            # «#Если Сервер» без внешнего соединения COM не проверить — только клиентом с YAxUnit
            ctx.results += [result("plan", "probe", m, "Server", "skipped",
                                   f"сервер без внешнего соединения — не найдено: {checks.DEPS['yaxunit']}")
                            for m in plain]
        if not (run_server or run_queries):
            return None
        return {"server": run_server, "modules": [m for m in modules if suitable(m)] if run_server else [],
                "session": session if run_server else [],
                "queries": queries if run_queries else [], "progid": progid, "note": note, "ecOff": ec_off}

    def run(self, ctx, items):
        note = items["note"]
        if ctx.have["com"]:
            try:
                results, run_note = run(ctx.platform, ctx.plan, items["modules"], items["queries"], items["progid"])
            except Unavailable as e:
                ctx.have["com"], note = False, str(e)
            else:
                if not any(r["kind"] == "session" for r in results):     # подключилось — модуль скомпилирован
                    results += [result(NAME, "session", m, "ExternalConnection", "ok") for m in items["session"]]
                return results, run_note
        if ctx.have["yaxunit"]:
            # модуль только внешнего соединения в контексте «Сервер» не пробуется — он «не проверено» ниже
            ctx.hand(self.fallback, "server", [m for m in items["modules"] if m["contexts"] != ["ExternalConnection"]])
            ctx.hand(self.fallback, "queries", items["queries"])
            if items["ecOff"]:
                return [], f"{note} — серверные пробы и запросы через YAxUnit"
            # YAxUnit компилирует в контексте «Сервер», без внешнего соединения
            lost = "теряется: ошибки компиляции кода внешнего соединения"
            skipped = [result(NAME, "session", m, "ExternalConnection", "skipped", f"{note}; {lost}")
                       for m in items["session"]]
            skipped += [result(NAME, "probe", m, "ExternalConnection", "skipped",
                               f"{note}; код под условием «ВнешнееСоединение» проверяется только через COM — {lost}")
                        for m in items["modules"] if "ExternalConnection" in m.get("preprocessorContexts", ())
                        or m["contexts"] == ["ExternalConnection"]]
            skipped = [{**r, "check": "probes-server"} for r in skipped]       # видно в JUnit и Problems
            return skipped, f"{note} — серверные пробы и запросы через YAxUnit"
        for name, wanted in (("probes-server", items["server"]), ("queries", bool(items["queries"]))):
            if wanted:
                ctx.gate.lack(name, f"{note}; {checks.DEPS['yaxunit']}", "no-yaxunit")
        return [], None
