"""Сводка прогона (спецификация gate-report): Markdown, JSON, JUnit, SARIF и строки для Problems VS Code.

Находка сводки: {"severity" (error|warning), "step", "rule", "file" (путь от корня репозитория),
"line", "column", "message", "sources"} и, если модуль известен, "object", "method", "place", "text".
Ошибки — error статики и compile-error/unknown проб; предупреждения — warning статики, runtime-error проб и ошибки
разбора запросов из `query_downgrades` (не менялись правкой, собраны подстановкой). Одна ошибка одного места от
нескольких проверок — одна находка, все проверки — в `sources`, первая — основная (`step`, `code`, текст);
ошибки компиляции — по тексту без префикса места пробы и суффикса режимов extcheck.
Отдельно: «не покрыто этим прогоном» с тем, чем закрыть, «не проверено» (неполные запросы, пробы,
не выполненные из-за падения сеанса), время шагов.

Сводка собирается только из файлов шагов (`otk.json`, `static.json`, `run.json`, `designer.json`): решения
реестра проверок (`notChecked`, `infra`) и примечания каждый шаг пишет в свой файл; зависимые модули — `static.json`
`dependents`. Шаг designer (gate --designer) — проверка всех модулей Конфигуратором: его новые относительно
базовой линии ошибки — находки (в той же строке, что ошибка другой проверки, — её источник), а выполненная целиком
проверка снимает «не покрыто» и «не проверено» с `needs: designer:<контекст>` в своих режимах.
Файл шага хранит отпечаток плана (`plan`, model.plan_id): файл от другого плана не учитывается. Метод и строка
кода находки — из проверенной редакции (read), без неё — только объект и место.
"""
import collections
import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape

from .. import __version__, lang, model
from ..probes.platform import CHECK_MODES
from ..public import public_summary as public_summary
from ..sources import bsl, contexts, layout
from . import reasons

PROBE_FAIL = model.FAIL
_POS = re.compile(r"\((\d+),(\d+)\)")
# префикс ошибки компиляции пробы: «[<расширение> ]<модуль>(строка,колонка): »
_COMPILE_PREFIX = re.compile(r"^(?:\S+ )?\S+?\(\d+,\d+\):\s*")
TEXT_MAX = 120
SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"

# справочник находок: раздел — код статики, `bslls`, вид пробы (`probe/compile-error` -> probe) или шаг
# публичного репозитория: лабораторный адрес в сводке не нужен пользователю
REPO_URL = "https://github.com/cmapnep-tools/otk-1c"
FINDINGS_DOC = f"{REPO_URL}/blob/master/docs/findings.md"
# имя проверки-источника в сводке
EXECUTOR_NAMES = {"com": "проба COM", "yaxunit": "проба YAxUnit", "extcheck": "Конфигуратор (временное расширение)",
                  "scenarios": "сценарный тест", "smoke": "открытие формы", "build": "загрузка в ИБ", "plan": "план",
                  "designer": "полная проверка Конфигуратором"}
CONTEXT_NAMES = {**contexts.NAMES, "OrdinaryClient": "толстый клиент (обычное приложение)", "*": "все режимы"}
# чем закрыть «не покрыто»: заголовок группы и подсказка
NEEDS_TITLES = {
    "designer": ("Полная проверка Конфигуратором",
                 "закроет `otk gate --designer` или `otk designer-check` (в CI — задание "
                 "otk-designer-check)"),
    "ui": ("UI и сценарные тесты", "модуль команды проверяется исполнением команды"),
    "publication": ("Проверка с публикацией", "модуль HTTP- и веб-сервиса проверяется на опубликованной базе"),
}


def query_downgrades(plan):
    """{id запроса: пояснение}: ошибка разбора такого запроса — предупреждение, а не ошибка.
    Запрос не менялся правкой (и метаданные не менялись) — ошибка была и до неё (типовой универсальный код
    обращается к объектам, которых в этой конфигурации нет); текст собран подстановкой — ошибка может быть от неё."""
    out = {}
    for q in plan.get("queries", []):
        if q.get("unchanged") and not plan.get("metadata"):
            out[q["id"]] = "запрос правкой не менялся — ошибка была и до неё"
        elif q.get("resolver"):          # внешний восстановитель может ошибиться
            out[q["id"]] = f"текст восстановлен «{q['resolver']}»" + (f" ({q['rationale']})" if q.get("rationale")
                                                                      else "")
        elif q.get("assembled"):
            out[q["id"]] = "текст собран с подстановкой"
    return out


# диагностики BSL LS, которые платформа ловит сама при компиляции модуля (замер на 8.3.27). Не дубли:
# UnknownPreprocessorSymbol (платформа молча выкидывает блок) и ThisObjectAssign (компилируется)
BSLLS_COMPILE = {f"{p}:{c}" for p in ("bslls", "bsla") for c in ("ParseError", "ProcedureReturnsValue")}
# находки анализаторов BSL (analyzers.py): код <префикс>:<правило>
ANALYZER_PREFIXES = {"bslls": "BSL LS", "bsla": "bsl-analyzer"}


def analyzer_of(code):
    """Префикс анализатора BSL в коде находки или None."""
    prefix = code.split(":", 1)[0]
    return prefix if ":" in code and prefix in ANALYZER_PREFIXES else None
COMPILE_KINDS = {"probe", "extcheck", "load", "session"}


def platform_duplicates(plan, static_findings, results):
    """Находки статики, которые повторяет платформа в пробах. Возвращает ({id() находки статики:
    результат пробы, к которому она добавляется источником, или None — отбросить}, {id() результата пробы: находка
    статики, которая его заменяет}).
    BSL LS: ошибка компиляции в модуле, где платформа сама нашла ошибку компиляции, — дубль (текст платформы
    точнее; источник — у ошибки платформы в той же строке, иначе у первой ошибки модуля); ошибка разбора запроса,
    который разобрала платформа, — дубль (источник у ошибки платформы) или ложная (платформа разобрала без ошибок).
    Своя статика и ошибка компиляции платформы в той же строке — одна находка, наша: текст подробнее."""
    compile_err = collections.defaultdict(list)
    for r in results:
        if r["kind"] in COMPILE_KINDS and r["outcome"] == "compile-error":
            compile_err[r.get("module")].append(r)
    spans = collections.defaultdict(list)
    queries = {q["id"]: q for q in plan.get("queries", [])}
    for r in results:
        q = queries.get(r.get("query"))
        if r["kind"] == "query" and r["outcome"] in ("ok", "compile-error") and q:
            spans[q["module"]].append((q["line"], q["line"] + (q.get("text") or "").count("\n"),
                                       r if r["outcome"] == "compile-error" else None))
    dropped, own = {}, {}
    for f in static_findings:
        line = f.get("line") or 0
        if f["check"] in BSLLS_COMPILE and f["module"] in compile_err:
            same = [r for r in compile_err[f["module"]] if r.get("line") == f.get("line")]
            dropped[id(f)] = (same or compile_err[f["module"]])[0]
        elif f["check"] in ("bslls:QueryParseError", "bsla:QueryParseError") \
                and (hit := [s for s in spans[f["module"]] if s[0] <= line <= s[1]]):
            dropped[id(f)] = next((s[2] for s in hit if s[2]), None)
        elif f["severity"] == "error" and not analyzer_of(f["check"]):
            own.setdefault((f["module"], f.get("line")), f)
    replaced = {id(r): own[(r.get("module"), r.get("line"))] for rs in compile_err.values() for r in rs
                if (r.get("module"), r.get("line")) in own}
    return dropped, replaced


def _static_source(f):
    return {"step": "static", "rule": f["check"], "message": f["message"]}


def _run_source(r):
    return {"step": f"run:{r['executor']}", "rule": f"{r['kind']}/{r['outcome']}", "context": r.get("context"),
            "message": r["message"] or r["outcome"]}


def compile_text(message):
    """Текст ошибки компиляции без префикса места пробы: «демо ОбщийМодуль.Х.Модуль(5,6): Т» -> «Т»."""
    return _COMPILE_PREFIX.sub("", " ".join((message or "").split()), count=1)


def same_compile_error(a, b):
    """Одна ошибка компиляции от разных проверок: тексты без префикса места равны или один — другой плюс
    « (режимы)» extcheck. «… (Х)» и «… (Y)» — разные ошибки."""
    a, b = compile_text(a), compile_text(b)
    return a == b or b.startswith(a + " (") or a.startswith(b + " (")


def _add_source(finding, source):
    if source not in finding["sources"]:
        finding["sources"].append(source)


def collect_findings(plan, static, run, notes=None):
    """Находки сводки с источниками; notes — список, куда пишется число отброшенных ложных находок."""
    src = plan["source"]
    downgrade = query_downgrades(plan)
    results = (run or {}).get("results", [])
    static_findings = (static or {}).get("findings", [])
    dropped, replaced = platform_duplicates(plan, static_findings, results)
    out, of_result = [], {}
    for f in static_findings:
        if id(f) in dropped:
            continue
        item = {"severity": f["severity"], "step": "static",
                "rule": f["check"], "file": f"{src}/{f['module']}", "line": f.get("line") or 1, "column": 1,
                "message": f["message"], "sources": [_static_source(f)], "_module": f["module"],
                "_line": f.get("line")}
        out.append(item)
        for r in results:
            if replaced.get(id(r)) is f:
                _add_source(item, _run_source(r))
                of_result[id(r)] = item
    # одна ошибка одного места от разных исполнителей и контекстов — одна находка с источниками; разные запросы
    # в одной строке — разные находки. Ошибки компиляции — по тексту без префикса места и суффикса режимов
    by_place, compiled = {}, collections.defaultdict(list)
    for r in results:
        if id(r) in replaced:
            continue
        if r["outcome"] in PROBE_FAIL or r["outcome"] == "runtime-error":
            col = _POS.search(r["message"] or "")
            why = downgrade.get(r.get("query"))
            item = {"severity": "error" if r["outcome"] in PROBE_FAIL and not why else "warning",
                    "step": f"run:{r['executor']}", "rule": f"{r['kind']}/{r['outcome']}",
                    "file": r.get("file") or (f"{src}/{r['module']}" if r.get("module") else None),
                    "line": r.get("line") or 1, "column": int(col.group(2)) if col else 1,
                    "context": r.get("context"),
                    "message": (f"{why}: " if why else "") + (r["message"] or r["outcome"]),
                    "sources": [_run_source(r)], "_module": r.get("module"), "_owner": r.get("owner"),
                    "_line": r.get("line")}
            key = (item["severity"], item["file"], item["line"], item["message"], r.get("query"))
            place = (item["severity"], item["file"], item["line"], r.get("query"))
            hit = by_place.get(key)
            if hit is None and r["kind"] in COMPILE_KINDS and r["outcome"] == "compile-error":
                hit = next((x for x in compiled[place] if same_compile_error(x["message"], item["message"])), None)
            if hit is not None:
                _add_source(hit, item["sources"][0])
                of_result[id(r)] = hit
                continue
            by_place[key] = of_result[id(r)] = item
            if r["kind"] in COMPILE_KINDS and r["outcome"] == "compile-error":
                compiled[place].append(item)
            out.append(item)
    false_parse = 0
    for f in static_findings:
        if id(f) not in dropped:
            continue
        target = of_result.get(id(dropped[id(f)])) if dropped[id(f)] is not None else None
        if target is not None:
            _add_source(target, _static_source(f))
        else:
            false_parse += 1
    if false_parse and notes is not None:
        notes.append(f"BSL LS: ошибок разбора запросов, которые платформа разобрала без ошибок, отброшено: "
                     f"{false_parse}")
    # одна ошибка статики в одном месте — одна находка (запрос из частей разбирается несколько раз: УНФ — до 34
    # повторов); пробы разных запросов в одной строке — разные находки
    seen, uniq = {}, []
    for f in out:
        key = (f["severity"], f["step"], f["rule"], f["file"], f["line"], f["message"])
        if f["step"] == "static" and key in seen:
            seen[key]["count"] = seen[key].get("count", 1) + 1
        else:
            seen[key] = f
            uniq.append(f)
    return uniq


def merge_designer(findings, designer, src):
    """Новые ошибки шага designer в находки сводки: ошибка в той же строке того же файла, что ошибка другой
    проверки, — источник этой находки, иначе своя находка (модуль вне плана — объект и место из сообщения
    Конфигуратора)."""
    by_place = {(f["file"], f["line"]): f for f in findings if f["severity"] == "error" and f.get("file")}
    prefix = src.strip("/") + "/"
    for d in (designer or {}).get("findings", []):
        source = {"step": "designer", "rule": "load/compile-error" if d.get("load") else "designer/compile-error",
                  "message": d["message"], **({"modes": d["modes"]} if d.get("modes") else {})}
        hit = by_place.get((d["file"], d["line"])) if d.get("file") else None
        if hit is not None:
            _add_source(hit, source)
            continue
        rel = d["file"][len(prefix):] if d.get("file", "") and d["file"].startswith(prefix) else None
        item = {"severity": "error", "step": "designer", "rule": source["rule"], "file": d.get("file"),
                "line": d["line"] or 1, "column": d.get("col") or 1, "message": d["message"], "sources": [source],
                "_module": rel, "_line": None if d.get("load") else d["line"],
                "_object": None if d.get("load") or "." not in d["object"] else d["object"]}
        findings.append(item)
        if d.get("file"):
            by_place[(d["file"], d["line"])] = item
    return findings


def designer_closes(designer):
    """needs -> закрыт ли выполненной проверкой всех модулей: designer:* — да, designer:<контекст> — если его режим
    был среди режимов проверки; не выполнялась — ничего. У designer:* с ctx — закрыт ли в этом контексте (сокращённый
    набор режимов проверяет модуль целиком только в них)."""
    if not (designer or {}).get("ran"):
        return lambda needs, ctx=None: False
    modes = set(designer.get("modes") or [])
    by_ctx = {ctx: key for key, ctx in CHECK_MODES if ctx}

    def closes(needs, ctx=None):
        group, _, need = (needs or "").partition(":")
        if group != "designer":
            return False
        if need == "*":
            return ctx is None or by_ctx.get(ctx) in modes
        return by_ctx.get(need) in modes
    return closes


def _module_names():
    """Вид модуля -> имя модуля в Конфигураторе (ObjectModule -> МодульОбъекта)."""
    names = {kind: name for name, kind in lang.platform_texts()["modules"].items()}
    for kind in ("CommonModule", "HTTPService", "WebService"):
        names[kind] = names["Module"]
    return names


def place_of(entry, line):
    """(объект 1С, место для «Перейти к строке» Конфигуратора) модуля плана: ОбщийМодуль.Х, ОбщийМодуль.Х.Модуль(120);
    line=None — модуль без строки. Вид модуля неизвестен (сценарий, файл загрузки, Unknown) — (None, None)."""
    kind, owner = entry.get("kind"), entry.get("owner")
    name = _module_names().get(kind)
    if not owner or not name:
        return None, None
    obj = "Конфигурация" if owner == kind else owner          # модуль корня конфигурации
    return obj, f"{obj}.{name}" + (f"({line})" if line else "")


def locate(findings, plan, read=None):
    """Объект, метод, место и строка кода находок. read(путь модуля) -> текст проверенной редакции или None."""
    entries = {m["path"]: m for m in plan["modules"]}
    parsed = {}
    # находка без строки — место модуля без номера, без метода и строки кода

    def module(path):
        if path not in parsed:
            text = read(path) if read else None
            parsed[path] = bsl.parse(text) if text is not None else None
        return parsed[path]

    for f in findings:
        path, line = f.pop("_module", None), f.pop("_line", None)
        f.pop("_owner", None)
        designer_obj = f.pop("_object", None)
        entry = entries.get(path)
        if not entry and designer_obj:
            # модуль Конфигуратора «ОбщийМодуль.Х.Модуль»: объект ОбщийМодуль.Х, место ОбщийМодуль.Х.Модуль(120)
            obj, place = designer_obj.rsplit(".", 1)[0], designer_obj + (f"({line})" if line else "")
        elif not entry:
            continue
        else:
            obj, place = place_of(entry, line)
        if obj:
            f["object"] = obj
        if place:
            f["place"] = place
        mod = module(path) if line and path else None
        if mod is None:
            continue
        m = mod.method_at(f["line"])
        if m:
            f["method"] = m.name
        lines = mod.text.split("\n")             # не splitlines: \x0c, \u2028 в литерале не сдвигают строки
        if 0 < f["line"] <= len(lines) and (code := " ".join(lines[f["line"] - 1].split())):
            f["text"] = code if len(code) <= TEXT_MAX else code[:TEXT_MAX - 1] + "…"
    return findings


def needs_of(r):
    """Чем закрыть пропуск пробы: компиляцию — полная проверка Конфигуратором в её контексте; запрос — только
    исполнение кода (Конфигуратор текст запроса не разбирает), а при сбое окружения («не выполнено: …» —
    COM, сеанс YAxUnit) — повторный прогон, а не другая проверка (None)."""
    if r["kind"] == "query":
        return None if str(r.get("message", "")).startswith("не выполнено:") else "ui"
    if r["kind"] not in COMPILE_KINDS:
        return None
    ctx = r.get("context")
    return f"designer:{'*' if ctx in (None, 'Designer') else ctx}"


def not_checked(plan, run):
    items = [{"what": "запрос", "file": f"{plan['source']}/{q['module']}", "line": q["line"],
              "reason": q.get("reason") or "текст собран с переменными частями", "needs": "ui"}
             for q in plan["queries"] if not q["complete"]]
    for r in (run or {}).get("results", []):
        if r["outcome"] == "skipped" and r["kind"] != "uncovered":
            items.append({"what": f"{r['kind']} [{r['executor']}/{r['context']}]",
                          "file": f"{plan['source']}/{r['module']}" if r.get("module") else r.get("owner"),
                          "line": r.get("line"), "reason": r["message"],
                          **({"needs": needs} if (needs := needs_of(r)) else {}),
                          **({"check": r["check"]} if r.get("check") else {})})
    return items


def uncovered(plan, run):
    """«Не покрыто этим прогоном»: что этот прогон не проверил по пределу метода и чем это закрыть."""
    return [{"owner": r["owner"], "file": f"{plan['source']}/{r['module']}" if r.get("module") else None,
             "context": r["context"], "needs": r["needs"], "reason": r["message"]}
            for r in (run or {}).get("results", []) if r["kind"] == "uncovered"]


def checked_count(static, run, designer=None):
    probes = sum(1 for r in (run or {}).get("results", []) if r["outcome"] not in ("skipped",))
    return probes + (1 if static is not None else 0) + (1 if (designer or {}).get("ran") else 0)


def verdict(plan, findings, static, run, infra_errors, designer=None, missing=()):
    has_input = bool(plan["modules"] or plan["queries"] or plan.get("smoke"))
    if infra_errors:
        return 2, "сбой инфраструктуры"
    if any(f["severity"] == "error" for f in findings):
        return 1, "есть ошибки — не отправлять"
    if has_input and checked_count(static, run, designer) == 0:
        return 2, "ничего не проверено"
    if missing:
        return 2, "не покрыто обязательное: " + ", ".join(missing)
    if not has_input and not plan["removedModules"] and not plan["metadata"]:
        return 0, "нечего проверять"
    return 0, "можно отправлять"


def missing_required(plan, closes, uncovered_, not_checked_=(), run=None):
    """Обязательные контексты (`environment.requiredContexts`) без покрытия. Порядок — как в настройке.

    Покрытие — по выполненным проверкам каждой пары «модуль × контекст»: проба в этом контексте, расширенная
    проверка в нём (run.extCheck.coverage) или полная проверка Конфигуратором в его режиме. Записи «не покрыто» и
    «не проверено» — то, что этими проверками не закрыто; само отсутствие записей покрытия не подтверждает: при
    `--static-only` пробы не выполнялись, и записей о непокрытых модулях нет."""
    required = list(plan.get("requiredContexts") or [])
    if not required:
        return []
    # контексты модуля по его пути: запись «не покрыто» с context "*" — модуль без пробы, не проверен целиком
    contexts_of = {m["path"]: set(m.get("contexts") or []) for m in plan["modules"]}
    prefix = plan["source"].rstrip("/") + "/"
    ext = (run or {}).get("extCheck") or {}
    # coverage — пары «модуль × контекст»; run.json прежней версии их не знает — там модуль проверен во всех
    # контекстах из extCheck.contexts
    coverage = ext.get("coverage")
    ext_pairs = ({i.get("module"): set(i.get("contexts") or ()) for i in coverage}
                 if coverage is not None else None)
    ext_modules, ext_contexts = set(ext.get("modules") or ()), set(ext.get("contexts") or ())

    def about_ctx(entry, ctx):
        if entry.get("context") == ctx:
            return True
        if entry.get("context") != "*":
            return False
        rel = (entry.get("file") or "")[len(prefix):] if (entry.get("file") or "").startswith(prefix) else None
        return ctx in contexts_of.get(rel, ())

    # контексты заимствованного общего модуля без --parent неизвестны: он может быть в любом требуемом контексте,
    # и требование по нему подтвердить нечем (запись «не покрыто» с needs designer:* это и означает)
    unknown_modules = [m for m in plan["modules"]
                       if m.get("kind") == "CommonModule" and (m.get("probe") or {}).get("strategy") == "none"]

    def ext_checked(path, ctx):
        """Расширенная проверка подтвердила компиляцию этой пары «модуль × контекст»."""
        if ext_pairs is None:              # run.json прежней версии: модуль проверен во всех режимах из contexts
            return path in ext_modules and ctx in ext_contexts
        return ctx in ext_pairs.get(path, ())

    def checked(module, ctx):
        """Выполненная проверка модуля в контексте: проба или сеанс этого модуля (пропуск — не проверка),
        расширенная проверка в этом режиме, полная проверка Конфигуратором в режиме контекста. Результат
        запроса проверяет текст запроса, а не модуль, поэтому покрытием не считается."""
        if closes("designer:*", ctx):
            return True                    # полная проверка Конфигуратором проверяет все модули конфигурации
        if ext_checked(module["path"], ctx):
            return True                    # расширенная проверка в этом режиме
        return any(r.get("kind") in ("probe", "session") and r.get("module") == module["path"]
                   and r.get("context") == ctx and r.get("outcome") not in ("skipped", "uncovered")
                   for r in (run or {}).get("results", []))

    def module_of(entry):
        file = entry.get("file") or ""
        return file[len(prefix):] if file.startswith(prefix) else None

    out = []
    for ctx in required:
        modules = [m for m in plan["modules"] if ctx in (m.get("contexts") or [])]
        # у модуля с неизвестными контекстами подтвердить контекст может только проверка Конфигуратором в его
        # режиме: расширенная проверка во временном расширении его флаги не задаёт, проба не выполнялась
        unknown = [] if closes("designer:*", ctx) else unknown_modules
        if not modules and not unknown:
            continue
        done = {m["path"] for m in modules if checked(m, ctx)}
        # designer:* закрывает wildcard-запись только если режим этого контекста тоже был в проверке; запись
        # о непроверенном модуле не считается, если этот модуль проверен другой выполненной проверкой
        lost = [i for i in uncovered_ if about_ctx(i, ctx) and not closes(i.get("needs"), ctx)
                and module_of(i) not in done]
        lost += [i for i in not_checked_ if i.get("needs") == f"designer:{ctx}"
                 and not closes(i.get("needs"), ctx)]
        if unknown or lost or any(m["path"] not in done for m in modules):
            out.append(ctx)
    return out


def build(plan, static, run, timings, infra_errors=(), read=None, designer=None):
    """Сводка по файлам шагов: plan — otk.json, static/run/designer — static.json/run.json/designer.json или
    None (шаг не выполнялся); infra_errors — сбои вне файлов шагов; read(путь модуля) -> текст проверенной редакции
    (метод и строка кода находок) или None."""
    static_ = static or {}
    run_ = run or {}
    designer_ = designer or {}
    plan = model.with_dependents(plan, static)
    infra = [reasons.entry(x) for x in (list(infra_errors) + static_.get("infra", []) + run_.get("infra", [])
                                        + designer_.get("infra", []))]
    bad = contexts.values_error(plan["requiredContexts"]) if "requiredContexts" in plan else None
    if bad:      # план не проходит свою схему: неверное значение пропустило бы непокрытый контекст
        infra.append(reasons.entry({"message": f"requiredContexts плана: {bad}", "reasonCode": "config-invalid"}))
    avail_bad = contexts.values_error(plan["contextsAvailable"]) if "contextsAvailable" in plan else None
    if avail_bad:      # контексты модулей включают WebClient, а доступные для проверки — нет
        infra.append(reasons.entry({"message": f"contextsAvailable плана: {avail_bad}",
                                    "reasonCode": "config-invalid"}))
    dropped_notes = []
    findings = merge_designer(collect_findings(plan, static, run, dropped_notes), designer, plan["source"])
    findings = locate(findings, plan, read)
    closes = designer_closes(designer)
    not_checked_ = [{**i, "reasonCode": i.get("reasonCode") or reasons.classify(i.get("reason"))}
                    for i in static_.get("notChecked", []) + run_.get("notChecked", []) + not_checked(plan, run)]
    uncovered_ = uncovered(plan, run)
    missing = [] if bad else missing_required(plan, closes, uncovered_, not_checked_, run_)
    code, text = verdict(plan, findings, static, run, infra, designer, missing)
    an_info = static_.get("analyzerInfo") or {}
    bslls_error = an_info.get("error")
    an_name = "BSL LS" if an_info.get("analyzer", "bslls") == "bslls" else \
        "bsl-analyzer" if an_info["analyzer"] == "bsl-analyzer" else "анализатор BSL"
    only = an_info.get("onlyIn") or {}
    an_notes = list(an_info.get("notes", [])) + (
        ["только у одного анализатора: " + ", ".join(f"{k} — {v}" for k, v in only.items())] if any(only.values())
        else [])
    notes = (list(plan.get("notes", [])) + ([f"{an_name} не выполнен: {bslls_error}"] if bslls_error else [])
             + an_notes
             + list((static_.get("apiInfo") or {}).get("notes", []))
             + list(run_.get("notes", [])) + list(designer_.get("notes", [])) + dropped_notes
             + [x["message"] for x in infra])
    # needs designer:* — модуль целиком: закрыт, только если в режимах проверки были все его контексты
    # (сокращённый набор режимов проверяет модуль частично, и «не покрыто» должно остаться)
    contexts_of = {m["path"]: list(m.get("contexts") or []) for m in plan["modules"]}
    unknown_paths = {m["path"] for m in plan["modules"]
                     if m.get("kind") == "CommonModule" and (m.get("probe") or {}).get("strategy") == "none"}
    required = [] if bad else list(plan.get("requiredContexts") or [])    # неверное значение уже дало config-invalid
    prefix = plan["source"].rstrip("/") + "/"

    def closes_item(i):
        needs = i.get("needs")
        if needs != "designer:*":
            return closes(needs)
        file = i.get("file") or ""
        rel = file[len(prefix):] if file.startswith(prefix) else None
        contexts = contexts_of.get(rel)
        if not contexts and rel in unknown_paths:
            # контексты неизвестны: запись закрыта, только если проверены все обязательные режимы
            return all(closes(needs, ctx) for ctx in required) if required else closes(needs)
        return closes(needs) if not contexts else all(closes(needs, ctx) for ctx in contexts)

    closed = sum(1 for i in not_checked_ + uncovered_ if closes_item(i))
    summary = {**model.header("summary"), "code": code, "verdict": text, "base": plan["base"], "head": plan["head"],
               "modules": len(plan["modules"]), "queries": len(plan["queries"]),
               "findings": findings,
               "notChecked": [i for i in not_checked_ if not closes_item(i)],
               "uncovered": [i for i in uncovered_ if not closes_item(i)], "notes": notes, "infra": infra,
               "scenarios": scenario_counts(plan, run), "smoke": smoke_counts(plan, run), "timings": timings}
    if designer is not None:
        summary["designer"] = {"ran": bool(designer_.get("ran")), "modes": designer_.get("modes", []),
                               "control": designer_.get("control"), "baseline": designer_.get("baseline"),
                               "known": designer_.get("known", 0), "fixed": designer_.get("fixed", 0),
                               "new": len(designer_.get("findings", [])), "closed": closed}
    return summary


def head_reader(tree, plan):
    """read(путь модуля) по корню проверенной редакции или None. tree — каталог или функция без аргументов, которая
    его даёт (workspace.tree_root: может выгрузить редакцию из git) — вызывается при первом чтении; не удалось
    (каталог прогона от удалённой редакции) — метода и строки кода нет."""
    if tree is None:
        return None
    state = {}

    def read(rel):
        if "root" not in state:
            try:
                state["root"] = Path(tree() if callable(tree) else tree) / plan["source"]
            except Exception:  # noqa: BLE001 — строка кода необязательна, сводка важнее
                state["root"] = None
        return layout.read_text(state["root"] / rel) if state["root"] else None
    return read


def from_files(run_dir, timings=None, tree=None):
    """Сводка по файлам шагов каталога прогона (otk report): нет static.json/run.json или он от другого
    плана — шаг не выполнялся. timings — время шагов от сценария; без него — время из run.json. tree — корень
    проверенной редакции (workspace.tree_root): метод и строка кода находок."""
    run_dir = Path(run_dir)
    plan = json.loads((run_dir / "otk.json").read_text(encoding="utf-8"))
    (static, stale_static), (run, stale_run), (designer, stale_designer) = (
        step_for(plan, run_dir / name) for name in ("static.json", "run.json", "designer.json"))
    if timings is None:
        timings = {f"run.{k}": v for k, v in (run or {}).get("timings", {}).items()}
    summary = build(plan, static, run, timings, read=head_reader(tree, plan), designer=designer)
    summary["notes"] += [n for n in (stale_static, stale_run, stale_designer, plan_version_note(plan)) if n]
    return summary


def plan_version_note(plan):
    """Примечание, если план записан другой версией ОТК (поля могли смениться: ciOnly -> uncovered)."""
    if plan.get("schema") != model.PLAN_SCHEMA:
        return (f"otk.json формата {plan.get('schema') or plan.get('version')}, ожидается {model.PLAN_SCHEMA}: "
                "план записан другой версией ОТК — перезапустите collect или gate")
    return None


def step_for(plan, path):
    """(файл шага или None, примечание): None — шаг не выполнялся или файл от другого плана (прошлый прогон,
    план пересобран collect) — тогда примечание, почему файл не учтён."""
    path = Path(path)
    if not path.exists():
        return None, None
    step = json.loads(path.read_text(encoding="utf-8"))
    if step.get("plan") != model.plan_id(plan):
        return None, f"{path.name} не учтён: записан для другого плана (перезапустите шаг)"
    return step, None


def smoke_counts(plan, run):
    """Дымовой тест форм: выбрано, открыто, пропущено (отказ без параметров), с ошибкой."""
    res = [r for r in (run or {}).get("results", []) if r.get("kind") == "smoke"]
    return {"selected": len(plan.get("smoke", [])), "opened": sum(1 for r in res if r["outcome"] == "ok"),
            "skipped": sum(1 for r in res if r["outcome"] == "skipped"),
            "failed": sum(1 for r in res if r["outcome"] not in ("ok", "skipped"))}


def scenario_counts(plan, run):
    """Сценарные тесты: тестов в проекте, выбрано, выполнено, упало."""
    res = [r for r in (run or {}).get("results", []) if r.get("kind") == "scenario"]
    return {"selected": len(plan.get("scenarios", [])), "run": len(res),
            "failed": sum(1 for r in res if r["outcome"] in PROBE_FAIL),
            "skipped": sum(1 for r in res if r["outcome"] == "skipped"),
            "extensions": len(plan.get("scenarioExtensions", []))}


def source_name(s):
    """Проверка-источник для человека: «статика (metadata-ref)», «проба YAxUnit (сервер)»."""
    if s["step"] == "static":
        prefix = analyzer_of(s["rule"])
        return (f"{ANALYZER_PREFIXES[prefix]} ({s['rule'].split(':', 1)[1]})" if prefix
                else f"статика ({s['rule']})")
    name = EXECUTOR_NAMES.get(s["step"].removeprefix("run:"), s["step"])
    ctx = CONTEXT_NAMES.get(s.get("context"))
    if s.get("modes"):
        ctx = ", ".join(m.lower() for m in s["modes"])
    return f"{name} ({ctx})" if ctx else name


def sources_text(f):
    return ", ".join(source_name(s) for s in f.get("sources") or [{"step": f["step"], "rule": f["rule"]}])


def where(f):
    """Объект и метод находки: ОбщийМодуль.Х.Метод."""
    return ".".join(x for x in (f.get("object"), f.get("method")) if x)


def needs_text(needs):
    group, _, ctx = needs.partition(":")
    title = NEEDS_TITLES.get(group, (needs, ""))[0]
    return f"{title} ({CONTEXT_NAMES.get(ctx, ctx)})" if ctx else title


def doc_anchor(code):
    """Раздел справочника находок docs/findings.md для кода: metadata-ref, bslls:ParseError -> bslls,
    probe/compile-error -> probe, not-checked/<проверка> -> not-checked."""
    if analyzer_of(code):
        return analyzer_of(code)
    return code.split("/", 1)[0]


def doc_link(code):
    return f"{FINDINGS_DOC}#{doc_anchor(code)}"


def finding_place(f):
    """Место находки: место Конфигуратора, иначе файл:строка, иначе объект или «без модуля»."""
    return f.get("place") or (f"{f['file']}:{f['line']}" if f.get("file") else f.get("object") or "без модуля")


def _finding_line(f, with_code=True):
    head = f"`{finding_place(f)}`"
    method = f" {f['method']}" if f.get("method") else ""
    tail = f" — `{f['file']}:{f['line']}`" if f.get("place") and f.get("file") else ""
    lines = [f"- {head}{method} — {f['message']} [`{f['rule']}`]({doc_link(f['rule'])}) _(найдено: {sources_text(f)})_"
             + (f" ×{f['count']}" if f.get("count") else "") + tail]
    if with_code and f.get("text"):
        lines.append(f"  `{f['text']}`")
    return lines


def designer_lines(d):
    """Строка сводки о шаге designer: режимы, контроль, базовая линия, что снято с «не покрыто»."""
    if not d:
        return []
    if not d["ran"]:
        return ["Проверка Конфигуратором: не выполнена (см. примечания)"]
    parts = [f"режимы {' '.join(d['modes'])}"]
    if d.get("control"):
        parts.append("отрицательный контроль " + ("сработал" if d["control"]["ok"] else "НЕ СРАБОТАЛ"))
    if d.get("baseline"):
        parts.append(f"базовая линия `{d['baseline']}`: принятых {d['known']}, исправлено {d['fixed']}")
    parts.append(f"новых ошибок {d['new']}")
    if d.get("closed"):
        parts.append(f"снято с «не покрыто» и «не проверено»: {d['closed']}")
    return ["Проверка Конфигуратором: " + "; ".join(parts)]


_SUGGESTION = re.compile(r" \(похоже: [^)]*\)$")
REPEATED_MIN = 3        # одна причина в стольких местах и больше — строкой «частые причины»
BRIEF_OVER = 50         # находок больше — в консоль только сводка (gate и report без --full)


def repeated(findings, minimum=REPEATED_MIN):
    """Частые причины ошибок: одна и та же ошибка (код и текст без подсказки «похоже») в minimum местах и больше —
    [(код, текст, мест, модулей)] по убыванию числа мест. 198 ссылок на один отсутствующий объект — одна строка."""
    groups = collections.defaultdict(list)
    for f in findings:
        if f["severity"] == "error":
            groups[(f["rule"], _SUGGESTION.sub("", str(f["message"])))].append(f)
    out = [(code_, message, len(items), len({i.get("file") or i.get("object") for i in items}))
           for (code_, message), items in groups.items() if len(items) >= minimum]
    return sorted(out, key=lambda g: (-g[2], g[0], g[1]))


def _repeated_lines(findings, limit=None):
    groups = repeated(findings)
    if not groups:
        return []
    shown = groups[:limit] if limit else groups
    lines = [f"Частые причины ({len(groups)}):"]
    lines += [f"- [`{code_}`]({doc_link(code_)}) {message} — мест: {n}, модулей: {mods}"
              for code_, message, n, mods in shown]
    if len(shown) < len(groups):
        lines.append(f"- … ещё {len(groups) - len(shown)}")
    return lines + [""]


def _head_lines(summary):
    # без git (gate --dir) без «было» — каталог целиком
    span = f"{summary['base']}..{summary['head']}" if summary["base"] else f"{summary['head']} (все модули)"
    lines = [f"# ОТК: {summary['verdict']} (код {summary['code']})", "",
             f"`{span}` — модулей: {summary['modules']}, запросов: {summary['queries']}"]
    sc = summary.get("scenarios") or {}
    if sc.get("extensions"):
        lines.append(f"Сценарии: выбрано {sc['selected']}, выполнено {sc['run']}, упало {sc['failed']}"
                     + (f", пропущено {sc['skipped']} (заготовки и пропуски)" if sc.get("skipped") else ""))
    sm = summary.get("smoke") or {}
    if sm.get("selected"):
        lines.append(f"Формы: выбрано {sm['selected']}, открыто {sm['opened']}, пропущено {sm['skipped']}, "
                     f"с ошибкой {sm['failed']}")
    return lines + designer_lines(summary.get("designer"))


def _timing_lines(summary):
    return (["", "## Время, с", "| Шаг | Время |", "| --- | --- |"]
            + [f"| {k} | {v} |" for k, v in summary["timings"].items()])


def brief(summary, top=10):
    """Сводка для консоли на большой разнице: счётчики по кодам, частые причины, объекты с наибольшим числом
    находок; подробности — в summary.md и summary.html."""
    summary = public_summary(summary)
    lines = _head_lines(summary) + [""]
    for title, severity in (("Ошибки", "error"), ("Предупреждения", "warning")):
        items = [f for f in summary["findings"] if f["severity"] == severity]
        codes = collections.Counter(f["rule"] for f in items)
        lines.append(f"## {title} ({len(items)})")
        lines.append(", ".join(f"`{c}` {n}" for c, n in codes.most_common()) if items else "нет")
        lines.append("")
        if severity == "error" and items:
            lines += _repeated_lines(items, top)
            objects = collections.Counter(f.get("object") or f.get("file") or "без модуля" for f in items)
            lines.append(f"Больше всего ошибок (объектов с ошибками: {len(objects)}):")
            lines += [f"- {obj} — {n}" for obj, n in objects.most_common(top)]
            lines.append("")
    lines.append(f"## Не проверено ({len(summary['notChecked'])}), не покрыто этим прогоном "
                 f"({len(summary['uncovered'])})")
    lines += [f"- {i['what']} — {i['reason']}" for i in summary["notChecked"] if not i.get("file")]
    if summary["notes"]:
        lines += ["", "## Примечания"] + [f"- {n}" for n in summary["notes"]]
    return "\n".join(lines + _timing_lines(summary)) + "\n"


def markdown(summary):
    summary = public_summary(summary)
    lines = _head_lines(summary)
    lines.append("")
    errors = [f for f in summary["findings"] if f["severity"] == "error"]
    warnings = [f for f in summary["findings"] if f["severity"] == "warning"]
    lines.append(f"## Ошибки ({len(errors)})")
    lines += _repeated_lines(errors)
    by_object = collections.defaultdict(list)
    for f in errors:
        by_object[f.get("object") or f.get("file") or "без модуля"].append(f)
    for obj, items in by_object.items():
        lines.append(f"### {obj}")
        for f in items:
            lines += _finding_line(f)
    if not errors:
        lines.append("нет")
    lines += ["", f"## Предупреждения ({len(warnings)})"]
    by_code = collections.defaultdict(list)
    for f in warnings:
        by_code[f["rule"]].append(f)
    for code_, items in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
        if len(items) > 3:
            lines.append(f"- [**{code_}**]({doc_link(code_)}) ×{len(items)}: {items[0]['message']} — "
                         + ", ".join(f"`{finding_place(i)}`" for i in items[:5])
                         + (" …" if len(items) > 5 else ""))
        else:
            for i in items:
                lines += _finding_line(i, with_code=False)
    if not warnings:
        lines.append("нет")
    lines += ["", f"## Не проверено ({len(summary['notChecked'])})"]
    lines += [f"- {i['what']}" + (f": `{i['file']}:{i['line']}`" if i.get("file") else "") + f" — {i['reason']}"
              + (f"; закроет: {needs_text(i['needs'])}" if i.get("needs") else "")
              for i in summary["notChecked"]] or ["нет"]
    lines += ["", f"## Не покрыто этим прогоном ({len(summary['uncovered'])})"]
    groups = collections.defaultdict(list)
    for i in summary["uncovered"]:
        groups[i["needs"].partition(":")[0]].append(i)
    for group, items in groups.items():
        title, hint = NEEDS_TITLES.get(group, (group, ""))
        lines.append(f"### {title}" + (f" — {hint}" if hint else ""))
        lines += [f"- {i['owner']} [{CONTEXT_NAMES.get(i['context'], i['context'])}] — {i['reason']}" for i in items]
    if not summary["uncovered"]:
        lines.append("нет")
    if summary["notes"]:
        lines += ["", "## Примечания"] + [f"- {n}" for n in summary["notes"]]
    return "\n".join(lines + _timing_lines(summary)) + "\n"


def console(summary, full=False):
    """Текст сводки для консоли: summary.md целиком, а при находках больше BRIEF_OVER (без full) — brief."""
    if full or len(summary["findings"]) <= BRIEF_OVER:
        return markdown(summary)
    return brief(summary) + f"\nнаходок {len(summary['findings'])} — полный список в summary.md и summary.html " \
                            "(в консоль целиком — --full)\n"


def _detail(f):
    """Объект, метод и источники находки в одной строке (Problems, JUnit)."""
    parts = [where(f)] if where(f) else []
    return " [" + "; ".join(parts + [f"найдено: {sources_text(f)}"]) + "]"


def problems(summary, repo):
    """Строки для problemMatcher VS Code: <абсолютный путь>(<строка>,<колонка>): <error|warning> <код>: <текст>."""
    summary = public_summary(summary)
    out = []
    for f in summary["findings"]:
        path = str((Path(repo) / f["file"]).resolve()) if f.get("file") else str(Path(repo).resolve())
        message = " ".join(str(f["message"]).split())
        out.append(f"{path}({f['line']},{f['column']}): {f['severity']} {f['rule']}: {message}{_detail(f)}")
    root = str(Path(repo).resolve())
    for i in summary.get("notChecked", []):
        if i.get("check"):           # решение реестра проверок (целиком или по модулю): видно и в Problems
            path = str((Path(repo) / i["file"]).resolve()) if i.get("file") else root
            out.append(f"{path}({i.get('line') or 1},1): warning not-checked/{i['check']}: {i['what']} — {i['reason']}")
    return out


def junit(summary):
    summary = public_summary(summary)
    cases = []
    for f in summary["findings"]:
        tag = "failure" if f["severity"] == "error" else "system-out"
        name = escape(f"{finding_place(f)} {f['rule']}", {'"': "&quot;"})
        body = escape(f["message"] + _detail(f) + f" (справка: {doc_link(f['rule'])})", {'"': "&quot;"})
        if tag == "failure":
            cases.append(f'<testcase classname="{escape(f["step"])}" name="{name}">'
                         f'<failure message="{body}">{body}</failure></testcase>')
        else:
            cases.append(f'<testcase classname="{escape(f["step"])}" name="{name}">'
                         f'<system-out>{body}</system-out></testcase>')
    skipped = [i for i in summary.get("notChecked", []) if i.get("check")]
    for i in skipped:
        body = escape(f"{i['what']} — {i['reason']}", {'"': "&quot;"})
        name = escape(f"{i['check']} {i['file']}" if i.get("file") else i["check"])
        cases.append(f'<testcase classname="checks" name="{name}"><skipped message="{body}"/></testcase>')
    if not cases:
        cases.append('<testcase classname="otk" name="проверка изменений"/>')
    fails = sum(1 for f in summary["findings"] if f["severity"] == "error")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<testsuite name="otk" tests="{len(cases)}" failures="{fails}" skipped="{len(skipped)}">\n'
            + "\n".join(cases) + "\n</testsuite>\n")


def sarif(summary, repo):
    """SARIF 2.1.0: code scanning GitHub/GitLab, просмотрщики SARIF в VS Code и других IDE.

    Пути — от корня репозитория (`uriBaseId` SRCROOT), правило — код находки со ссылкой на справочник; «не
    проверено» реестра проверок — результаты `kind: open` без уровня (проверка не выполнялась, это не ошибка)."""

    summary = public_summary(summary)
    codes = sorted({f["rule"] for f in summary["findings"]})
    rules = [{"id": c, "name": c, "shortDescription": {"text": c}, "helpUri": doc_link(c)} for c in codes]
    results = []
    for f in summary["findings"]:
        message = " ".join(str(f["message"]).split())
        result = {"ruleId": f["rule"], "ruleIndex": codes.index(f["rule"]),
                  "level": "error" if f["severity"] == "error" else "warning",
                  "message": {"text": message + _detail(f)},
                  "locations": [_sarif_location(f.get("file"), f.get("line"), f.get("column"), f.get("text"),
                                                where(f) or f.get("object"))],
                  "partialFingerprints": {"otk/v1": _fingerprint(f["rule"], f.get("file"), where(f), message)},
                  "properties": {"step": f["step"], "sources": sources_text(f)}}
        if f.get("place"):
            result["properties"]["place"] = f["place"]
        if f.get("count"):
            result["occurrenceCount"] = f["count"]
        results.append(result)
    for i in summary.get("notChecked", []):
        if i.get("check"):
            results.append({"ruleId": f"not-checked/{i['check']}", "kind": "open", "level": "none",
                            "message": {"text": f"{i['what']} — {i['reason']}"},
                            "locations": [_sarif_location(i.get("file"), i.get("line"), None, None, i["what"])]})
    run = {"tool": {"driver": {"name": "otk", "fullName": "ОТК — проверка изменений кода 1С",
                               "version": __version__, "informationUri": REPO_URL, "rules": rules}},
           "originalUriBaseIds": {"SRCROOT": {"uri": Path(repo).resolve().as_uri().rstrip("/") + "/"}},
           "invocations": [{"executionSuccessful": summary["code"] != 2}],
           "properties": {"verdict": summary["verdict"], "exitCode": summary["code"],
                          "base": summary.get("base"), "head": summary.get("head")},
           "results": results}
    return {"$schema": SARIF_SCHEMA, "version": "2.1.0", "runs": [run]}


def _sarif_location(file, line, column, text, logical):
    loc = {}
    if file:
        region = {"startLine": line or 1, "startColumn": column or 1}
        if text:
            region["snippet"] = {"text": text}
        loc["physicalLocation"] = {"artifactLocation": {"uri": urllib.parse.quote(file.replace("\\", "/")),
                                                        "uriBaseId": "SRCROOT"}, "region": region}
    if logical:
        loc["logicalLocations"] = [{"fullyQualifiedName": logical}]
    return loc


def _fingerprint(*parts):
    """Отпечаток находки без номера строки: правка выше по модулю не делает её новой."""
    return hashlib.sha256("\x1f".join(str(p or "") for p in parts).encode("utf-8")).hexdigest()[:32]
