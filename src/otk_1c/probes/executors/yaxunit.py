"""Исполнитель YAxUnit (тонкий клиент): клиентские модули, формы, серверные пробы без внешнего
соединения, запросы (если COM недоступен).

Ошибку компиляции в пробе YAxUnit возвращает как error теста (спайк D1). Падение при старте
(модуль сеанса, глобальные модули) — по журналу регистрации и /Out клиента. Закрывается только
свой процесс клиента.
"""
import json
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import ib_tools, journal
from .base import PROBE_METHOD, classify, compile_position, query_result, result

NAME = "yaxunit"
CONTEXTS = {"Server": "Сервер", "ThinClient": "КлиентУправляемоеПриложение",
            "OrdinaryClient": "КлиентОбычноеПриложение"}


CONFLICT_ATTEMPTS = 4     # запусков с переустановкой YAxUnit при конфликте имён


class ToolsConflict(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


def _check_of(item):
    """Проверка реестра, к которой относится проба build_items: запрос, серверная или клиентская компиляция."""
    _, ctx, spec = item
    return "queries" if spec["kind"] == "query" else "probes-server" if ctx == "Server" else "probes-client"


def build_items(modules, queries, server_plain, available=("Server", "ThinClient")):
    """Пробы для YAxUnit: [(элемент плана, контекст плана, проба для yax-plan.json)]."""
    items = []
    for entry in modules:
        probe = entry["probe"]
        contexts = [c for c in entry["contexts"] if c in ("ThinClient", "OrdinaryClient") and c in available]
        if entry["path"] in server_plain:
            contexts.append("Server")
        for ctx in contexts:
            if probe["strategy"] == "get-form":
                if ctx != "ThinClient":
                    continue
                spec = {"kind": "form", "expr": probe["target"], "expect": "none"}
            elif probe["strategy"] == "missing-method":
                spec = {"kind": "expr", "expr": f"{probe['target']}.{PROBE_METHOD}()", "expect": "missing"}
            elif probe["strategy"] == "create-object" and ctx == "Server":
                spec = {"kind": "expr", "expr": f"{probe['target']}.{probe['method']}()", "expect": "none"}
            else:
                continue
            spec_item = {"name": f"{len(items)}|{entry['owner']}|{ctx}", "context": CONTEXTS[ctx], **spec}
            items.append((entry, ctx, spec_item))
    for q in queries:
        entry = {"path": q["module"], "owner": q["id"], "query": q}
        items.append((entry, "Server", {"name": f"{len(items)}|{q['id']}|Server", "context": "Сервер",
                                        "kind": "query", "expr": q["text"], "expect": "none"}))
    return items


def build_smoke(forms):
    """Пробы дымового теста: открыть и закрыть каждую форму плана smoke (тонкий клиент)."""
    return [({"path": f["path"], "owner": f["form"]}, "ThinClient",
             {"name": f"{n}|{f['form']}|smoke", "context": CONTEXTS["ThinClient"], "kind": "open", "expr": f["form"],
              "expect": "none"}) for n, f in enumerate(forms)]


def launch(platform, ws, filters, timeout=600, stalled=None):
    """Клиент с YAxUnit: RunUnitTests по конфигурации с фильтром filters. Возвращает (путь отчёта jUnit,
    код клиента, ошибки журнала регистрации, вывод /Out). ToolsConflict — конфликт имён YAxUnit.
    stalled() -> True — клиент завис (дымовой тест: форма не закрылась), завершить его."""
    report, exit_file, out = ws / "report.xml", ws / "exit.txt", ws / "client_out.txt"
    cfg = {"filter": filters, "reportFormat": "jUnit", "reportPath": str(report),
           "closeAfterTests": True, "showReport": False, "exitCode": str(exit_file), "workspacePath": str(ws)}
    (ws / "yax.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    platform.unlock()
    before = len(journal.perform_errors(platform.ib))
    t0 = time.time()
    proc = platform.start_client("/DisableStartupDialogs", "/DisableStartupMessages", f"/Out{out}",
                                 f"/CRunUnitTests={ws / 'yax.json'}")
    errors, err_at = [], None
    while time.time() - t0 < timeout and proc.poll() is None:
        time.sleep(1)
        errors = journal.perform_errors(platform.ib)[before:]
        # ошибка внутри пробы (открытие формы) — тесты идут дальше; вне проб — признак фатального окна
        if any(ib_tools.MODULE not in e for e in errors) and err_at is None:
            err_at = time.time()
        if err_at and time.time() - err_at > 20:
            break                         # ошибка в журнале, а клиент не завершается — окно фатальной ошибки
        if stalled and stalled():
            break
    errors = journal.perform_errors(platform.ib)[before:]   # записанные к завершению клиента
    code = proc.poll()
    if code is None:
        proc.kill()
        try:
            proc.wait(timeout=30)          # иначе блокировки ИБ ещё держатся при следующем запуске
        except subprocess.TimeoutExpired:
            pass
    out_text = out.read_text(encoding="utf-8-sig", errors="replace").strip() if out.exists() else ""
    for e in errors:
        if (m := ib_tools.CONFLICT.search(e)):
            raise ToolsConflict(m.group(1))
    return report, code, errors, out_text


def form_error(errors, form):
    """Ошибка журнала регистрации из модуля формы form («ОбщаяФорма.Х», «Справочник.Х.Форма.Ф»): кадр стека
    «{<расширение> <форма>.Форма(<строка>)}». Возвращает (строка, текст) первой такой ошибки или None."""
    frame = re.compile(r"\{(?:[^{}\s]+ )?" + re.escape(form) + r"\.(?:Форма|Form)\((\d+)(?:,\d+)?\)\}")
    for e in errors:
        if ib_tools.MODULE in e and (m := frame.search(e)):
            first = e.strip().splitlines()[0].strip()
            return int(m.group(1)), f"{first} {m.group(0)}" if m.group(0) not in first else first
    return None


def _smoke_failure(entry, ctx, smoke_severity, message, line=None):
    return result(NAME, "smoke", entry, ctx, "failed" if smoke_severity == "error" else "runtime-error",
                  f"открытие формы {entry['owner']}: {message}", line)


def run(platform, items, run_dir, timeout=600):
    """Возвращает (результаты, примечание). ToolsConflict — нужна переустановка YAxUnit с переименованием."""
    if not items:
        return [], ""
    ws = Path(run_dir) / "yax"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    (ws / "yax-plan.json").write_text(json.dumps([i[2] for i in items], ensure_ascii=False), encoding="utf-8")
    report, code, errors, out_text = launch(platform, ws, {"extensions": [ib_tools.EXT]}, timeout)
    if report.exists():
        return _from_report(report, items, errors), ""
    return _no_report(items, code, errors, out_text)


def run_smoke(platform, items, run_dir, form_timeout=60, smoke_severity="error", timeout=600):
    """Дымовой тест: открыть и закрыть формы (build_smoke) отдельным запуском клиента. Возвращает (результаты,
    примечание). Ошибка при открытии/закрытии — failed (smoke_severity = "error") или runtime-error; форма
    отказалась открываться (Отказ) — skipped. Форма, не закрывшаяся за form_timeout секунд (модальное окно,
    вопрос пользователю, долгий обработчик), — ошибка; клиент завершается, остальные формы — следующим запуском.
    Ход виден по smoke-progress.txt (строки start|done|skip и имя формы)."""
    results, pending = [], list(items)
    for _ in range(len(items)):
        if not pending:
            break
        ws = Path(run_dir) / "smoke"
        shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        (ws / "yax-plan.json").write_text(json.dumps([i[2] for i in pending], ensure_ascii=False), encoding="utf-8")
        progress = ws / "smoke-progress.txt"

        def read_progress(progress=progress):
            """Строки хода; None — файл занят клиентом (дописывает строку), прочитать в следующий раз."""
            try:
                if not progress.exists():
                    return []
                return progress.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            except OSError:
                return None

        state = {"last": None, "since": time.time()}

        def stalled(state=state):
            lines = read_progress()
            if lines is None:
                return False
            last = lines[-1] if lines else None
            if last != state["last"]:
                state.update(last=last, since=time.time())
                return False
            return bool(last) and last.startswith("start|") and time.time() - state["since"] > form_timeout

        report, code, errors, out_text = launch(platform, ws, {"extensions": [ib_tools.EXT]}, timeout, stalled)
        if report.exists():
            results += _from_report(report, pending, errors, smoke_severity)
            break
        lines = read_progress() or []     # клиент завершён — файл свободен; на всякий случай без падения
        events = dict(reversed(ln.split("|", 1)) for ln in lines if "|" in ln)
        hung = lines[-1].partition("|")[2] if lines and lines[-1].startswith("start|") else None
        rest = []
        for entry, ctx, spec in pending:
            event = events.get(spec["expr"])
            if spec["expr"] == hung:
                results.append(_smoke_failure(entry, ctx, smoke_severity,
                                              f"форма не закрылась за {form_timeout} с — модальное окно, вопрос "
                                              "пользователю или долгий обработчик открытия"))
            elif event == "done":
                logged = form_error(errors, spec["expr"])
                results.append(_smoke_failure(entry, ctx, smoke_severity, logged[1], logged[0]) if logged
                               else result(NAME, "smoke", entry, ctx, "ok"))
            elif event == "skip":
                results.append(result(NAME, "smoke", entry, ctx, "skipped",
                                      "форма отказалась открываться без параметров (Отказ при создании)"))
            else:
                rest.append((entry, ctx, spec))
        if hung is None and rest:             # клиент не дошёл до этих форм или упал — причина как у проб
            res, note = _no_report(rest, code, errors, out_text)
            return results + res, note
        pending = rest
    return results, ""


def _from_report(report, items, errors, smoke_severity="error"):
    results = []
    by_name = {i[2]["name"]: i for i in items}
    for tc in ET.parse(report).getroot().iter("testcase"):
        key = next((n for n in by_name if n in (tc.get("name") or "") or n in (tc.get("classname") or "")), None)
        if key is None:
            continue
        entry, ctx, spec = by_name.pop(key)
        bad = [c for c in tc if c.tag in ("failure", "error")]
        kind = {"query": "query", "open": "smoke"}.get(spec["kind"], "probe")
        skipped = [c for c in tc if c.tag == "skipped"]
        if skipped and not bad:
            results.append(result(NAME, kind, entry, ctx, "skipped",
                                  " ".join((skipped[0].get("message") or skipped[0].text or "").split())[:300]))
            continue
        logged = form_error(errors, spec["expr"]) if kind == "smoke" else None
        if not bad and logged:
            # ошибку обработчика формы (ПриОткрытии, ПриЗакрытии…) платформа не передаёт в Открыть(),
            # а показывает окном и пишет в журнал регистрации — со стеком, где есть кадр формы
            results.append(_smoke_failure(entry, ctx, smoke_severity, logged[1], logged[0]))
            continue
        if not bad:
            results.append(result(NAME, kind, entry, ctx, "ok"))
            continue
        text = (bad[0].get("message") or "") + "\n" + (bad[0].text or "")
        if kind == "query":
            results.append(query_result(NAME, entry["query"], ctx, text))
        else:
            outcome, line, message = classify(text, entry)
            if kind == "smoke" and outcome == "runtime-error":
                results.append(_smoke_failure(entry, ctx, smoke_severity, message, line))
                continue
            results.append(result(NAME, kind, entry, ctx, outcome, message, line))
    for entry, ctx, spec in by_name.values():
        results.append(result(NAME, "smoke" if spec["kind"] == "open" else "probe", entry, ctx, "unknown",
                              "проба не попала в отчёт YAxUnit"))
    return results


def _no_report(items, code, errors, out_text):
    """Отчёта нет: сеанс не дошёл до тестов — причина и непроверенные пробы."""
    text = "\n".join(errors) or out_text
    pos = compile_position(text)
    skipped = [result(NAME, "query" if spec["kind"] == "query" else "probe",
                      {"path": entry.get("path"), "owner": entry.get("owner")}, ctx, "skipped",
                      "не выполнено: сеанс YAxUnit не стартовал") for entry, ctx, spec in items]
    if pos:
        return [result(NAME, "session", {"path": None, "owner": pos[0]}, "ThinClient", "compile-error",
                       f"{pos[0]}({pos[1]},{pos[2]}): {pos[3]}", pos[1])] + skipped, "сеанс YAxUnit не стартовал"
    return [result(NAME, "session", None, "ThinClient", "unknown",
                   f"нет отчёта YAxUnit (код клиента {code}); {' '.join(text.split())[:300]}")] + skipped, \
        "нет отчёта YAxUnit"


def resolving_conflicts(ctx, launch_fn, checks, what):
    """Запуск YAxUnit (launch_fn) с переустановкой и переименованием при конфликте имён с конфигурацией —
    для проб, дымового теста и сценариев. Попытки кончились — не молча: gate.failed по checks."""
    if not ctx.tools_conflict:
        names = []
        for attempt in range(CONFLICT_ATTEMPTS):
            try:
                return launch_fn()
            except ToolsConflict as c:
                names.append(c.name)
                if attempt + 1 < CONFLICT_ATTEMPTS:
                    ctx.log(f"конфликт имён YAxUnit с конфигурацией: {c.name} — переустановка с переименованием")
                    ctx.ensure_tools([c.name])
        ctx.tools_conflict = (f"конфликт имён YAxUnit с конфигурацией не устранён переименованием "
                              f"за {CONFLICT_ATTEMPTS} попытки (имена: {', '.join(dict.fromkeys(names))})")
    reason = ctx.tools_conflict
    for check in checks:
        ctx.gate.failed(check, reason)
    return [], f"{what}: {reason}"


class Probes:
    """Пробы в тонком клиенте: клиентские модули и формы, серверные модули без внешнего соединения и всё,
    что передал COM (ctx.handed["yaxunit"]: server — модули, queries — запросы)."""
    name = NAME
    checks = ("probes-client",)

    def select(self, ctx):
        modules = ctx.modules
        # модуль внешнего соединения клиент не компилирует — его проверяет подключение COM
        session_start = any(m["probe"]["strategy"] == "session-start" and m["kind"] != "ExternalConnectionModule"
                            for m in modules)
        work = build_items(modules, [], set(), ctx.plan["contextsAvailable"]) or session_start
        if work and ctx.gate.ready("probes-client", ctx.have):
            return {"client": True, "sessionStart": session_start}
        return None

    def run(self, ctx, items):
        handed = ctx.handed.get(NAME, {})
        client = bool(items and items["client"])
        available = [c for c in ctx.plan["contextsAvailable"] if client or c not in ("ThinClient", "OrdinaryClient")]
        probes = build_items(ctx.modules, handed.get("queries", []), {m["path"] for m in handed.get("server", [])},
                             available)
        if client and not probes and items["sessionStart"]:
            probes = [({"path": None, "owner": "старт сеанса"}, "ThinClient",
                       {"name": "0|старт сеанса|ThinClient", "context": "КлиентУправляемоеПриложение",
                        "kind": "expr", "expr": "Пустышка = Истина", "expect": "none"})]
        if not probes:
            return [], None
        ctx.ensure_tools()
        return resolving_conflicts(ctx, lambda: run(ctx.platform, probes, ctx.run_dir),
                                   dict.fromkeys(_check_of(item) for item in probes), "пробы YAxUnit не выполнены")


class Smoke:
    """Дымовой тест форм — своим запуском: зависшая форма (модальное окно) не должна стоить результатов проб."""
    name = "smoke"
    checks = ("smoke",)

    def select(self, ctx):
        forms = ctx.plan.get("smoke", []) if "ThinClient" in ctx.plan["contextsAvailable"] else []
        return forms if forms and ctx.gate.ready("smoke", ctx.have) else None

    def run(self, ctx, items):
        ctx.ensure_tools()
        smoke = ctx.cfg.get("smoke", {})
        return resolving_conflicts(ctx, lambda: run_smoke(ctx.platform, build_smoke(items), ctx.run_dir,
                                                          smoke.get("timeout", 60), smoke.get("severity", "error")),
                                   self.checks, "дымовой тест форм не выполнен")
