"""Анализ технологического журнала 1С: события, нормализация, агрегаты по контексту/запросу/ошибке/блокировке.

Подход — скрипты разбора ТЖ на bash/perl/gawk (склейка многострочных событий, нормализация, сумма/количество/
среднее/максимум, сортировка, топ N), перенесённые в Python без внешних утилит.

Формат: каталоги процессов (`rphost_1234`, `rmngr_…`, `1cv8c_…`) с файлами `ГГММДДЧЧ.log`; событие начинается
строкой `мм:сс.дробь-длительность,ИМЯ,уровень,свойство=значение,…` и продолжается до следующего начала.
Длительность: в 8.3 дробь — 6 знаков (микросекунды), в 8.2 — 4 знака (1/10000 с); приводится к микросекундам.
"""
import itertools
import os
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

_START = re.compile(r"^(\d\d):(\d\d)\.(\d{4,6})-(\d+),(\w+),(\d+)", re.M)
DB_EVENTS = ("DBMSSQL", "DBPOSTGRS", "DBORACLE", "DB2", "DBV8DBENG", "DBCOPIES")
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TEMP = re.compile(r"#?\btt\d+\b")
_NUM = re.compile(r"\(\d+\)")
_PARAMS = re.compile(r"(?m)^p_\d+: .*$\n?")


_PROP = re.compile(r"([^=,\r\n][^=,]*)=('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[^,]*)")


def parse_props(text):
    """Свойства события: к=з, к='з, с запятыми', к="в двойных"; удвоенная кавычка внутри — символ кавычки;
    значения многострочные. Одним регулярным выражением (на порядок быстрее посимвольного разбора)."""
    props = {}
    for m in _PROP.finditer(text):
        v = m.group(2)
        if v[:1] in ("'", '"'):
            q = v[0]
            v = v[1:-1].replace(q + q, q)
        props[m.group(1).strip()] = v
    return props


_PROP_BY_NAME: dict[str, re.Pattern] = {}


class _Props:
    """Свойства события по требованию: нужное свойство ищется отдельным выражением, без разбора всех."""
    __slots__ = ("body", "cache")

    def __init__(self, body):
        self.body, self.cache = body, {}

    def get(self, name, default=None):
        if name not in self.cache:
            rx = _PROP_BY_NAME.get(name)
            if rx is None:
                rx = _PROP_BY_NAME[name] = re.compile(
                    r"(?:^|,)" + re.escape(name) + r"=('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[^,]*)")
            m = rx.search(self.body)
            v = None
            if m:
                v = m.group(1)
                if v[:1] in ("'", '"'):
                    v = v[1:-1].replace(v[0] * 2, v[0])
            self.cache[name] = v
        v = self.cache[name]
        return default if v is None else v

    def __getitem__(self, name):
        v = self.get(name)
        if v is None:
            raise KeyError(name)
        return v

    def all(self):
        return parse_props(self.body)


def iter_files(paths, process=None):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs.sort()                                 # порядок обхода os.walk на Linux не алфавитный
            if process and not os.path.basename(root).lower().startswith(process.lower()):
                continue
            for f in sorted(files):
                if f.endswith(".log"):
                    yield os.path.join(root, f)


def _hour(path):
    m = re.match(r"(\d\d)(\d\d)(\d\d)(\d\d)\.log$", os.path.basename(path))
    return datetime(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else None


_CHUNK = 64 * 1024 * 1024
_START_B = re.compile(rb"\n\d\d:\d\d\.\d{4,6}-\d+,")


def chunks(path, size=None):
    """Байтовые диапазоны файла по границам событий: большой файл разбирается частями параллельно."""
    size = size or _CHUNK
    total = os.path.getsize(path)
    bounds = [0]
    with open(path, "rb") as f:
        pos = size
        while pos < total:
            f.seek(pos)
            m = _START_B.search(f.read(1024 * 1024))
            if not m:
                break
            bounds.append(pos + m.start() + 1)
            pos = bounds[-1] + size
    bounds.append(total)
    return [(a, b) for a, b in itertools.pairwise(bounds) if b > a]


def _read(path, rng=None):
    with open(path, "rb") as f:
        if rng:
            f.seek(rng[0])
            raw = f.read(rng[1] - rng[0])
        else:
            raw = f.read()
    return raw.decode("utf-8-sig", errors="replace")


def iter_events(paths, process=None, events=None, since=None, until=None, rng=None):
    """События: {time, process, event, level, duration (мкс), props, file}. since/until — datetime;
    rng — байтовый диапазон (для одного файла, см. chunks)."""
    want = {e.upper() for e in events} if events else None
    for path in iter_files(paths, process):
        hour = _hour(path)
        if hour and ((since and hour.replace(minute=59, second=59) < since) or (until and hour > until)):
            continue
        text = _read(path, rng)
        starts = list(_START.finditer(text))
        proc = os.path.basename(os.path.dirname(path))
        for k, m in enumerate(starts):
            name = m.group(5)
            if want and name.upper() not in want:
                continue
            frac = m.group(3)
            dur = int(m.group(4)) * (100 if len(frac) == 4 else 1)          # 8.2: 1/10000 с → мкс
            t = hour.replace(minute=int(m.group(1)), second=int(m.group(2))) if hour else None
            if t and ((since and t < since) or (until and t > until)):
                continue
            body = text[m.end():starts[k + 1].start() if k + 1 < len(starts) else len(text)].rstrip("\r\n")
            yield {"time": t, "process": proc, "event": name, "level": int(m.group(6)), "duration": dur,
                   "props": _Props(body), "file": path}


_NORM_CACHE: dict[str, str] = {}


def normalize(text):
    """Нормализация для группировки: GUID, (числа), временные таблицы, строки параметров, пробелы.
    Тексты запросов и контексты в ТЖ повторяются сотни тысяч раз — результат кэшируется."""
    if not text:
        return ""
    hit = _NORM_CACHE.get(text)
    if hit is not None:
        return hit
    if len(_NORM_CACHE) > 200000:
        _NORM_CACHE.clear()
    _NORM_CACHE[text] = res = _normalize(text)
    return res


def _normalize(text):
    text = _PARAMS.sub("", text.replace("﻿", ""))
    text = _GUID.sub("{GUID}", text)
    text = _NUM.sub("({NUM})", text)
    text = _TEMP.sub("{TempTable}", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _error_key(descr):
    """Текст ошибки без изменяющихся частей (как в ErrorParse): процессы, значения ключей, сеансы."""
    d = re.sub(r"\(идентификатор процесса \d+\) ", "", descr)
    d = re.sub(r"Повторяющееся значение ключа: \(.+?\)", "Повторяющееся значение ключа:", d)
    if re.search(r"[CС]еанс отсутствует или удален", d):
        d = "Сеанс отсутствует или удален"
    if "Сеанс работы завершен администратором" in d:
        d = "Сеанс работы завершен администратором"
    return normalize(d)


REPORTS = {
    "calls": "серверные вызовы CALL по контексту (value: duration | memory | memorypeak | inbytes | outbytes | cpu)",
    "queries": "запросы к СУБД (DBMSSQL, DBPOSTGRS, …) — mode: sql | sql+context | context",
    "sdbl": "запросы платформы SDBL — mode: sql | sql+context | context",
    "errors": "ошибки EXCP и ошибки запросов QERR по описанию (mode: descr | descr+context)",
    "locks": "ожидания на управляемых блокировках TLOCK с WaitConnections — по пространству и контексту",
    "timeouts": "таймауты TTIMEOUT и взаимоблокировки TDEADLOCK по контексту/описанию",
    "events": "сводка по типам событий: число и суммарная длительность (доля СУБД во времени вызовов)",
}
_VALUES = {"duration": None, "memory": "Memory", "memorypeak": "MemoryPeak", "inbytes": "InBytes",
           "outbytes": "OutBytes", "cpu": "CpuTime"}


def _key(report, ev, mode):
    p = ev["props"]
    ctx = normalize(p.get("Context", ""))
    if report == "calls":
        return (ctx or None) if ev["event"] == "CALL" else None
    if report in ("queries", "sdbl"):
        names = ("SDBL",) if report == "sdbl" else DB_EVENTS
        if ev["event"].upper() not in names:            # файловая база пишет «DBV8DBEng»
            return None
        sql = normalize(p.get("Sql") or p.get("Query") or "")
        if mode == "context":
            return ctx or None
        if mode == "sql+context":
            return f"{sql}\n--- Context:\n{ctx}" if (sql and ctx) else None
        return sql or None
    if report == "errors":
        if ev["event"] not in ("EXCP", "QERR") or not p.get("Descr"):
            return None
        d = _error_key(p["Descr"])
        return f"{d}\n--- Context:\n{ctx}" if mode == "descr+context" and ctx else d
    if report == "locks":
        if ev["event"] != "TLOCK" or not p.get("WaitConnections"):
            return None
        return f"{p.get('Regions', '')}\n--- Context:\n{ctx}"
    if report == "timeouts":
        if ev["event"] not in ("TTIMEOUT", "TDEADLOCK"):
            return None
        return f"{ev['event']}: {ctx or normalize(p.get('Descr', ''))}"
    if report == "events":
        return ev["event"]
    raise ValueError(f"неизвестный отчёт: {report}")


def _agg_file(job):
    """Агрегаты одного файла (в отдельном процессе): ({ключ: [сумма, количество, максимум]}, событий)."""
    path, rng, kind, mode, value, events, since, until = job
    agg, total = {}, 0
    prop = _VALUES[value]
    for ev in iter_events([path], None, events, since, until, rng):
        k = _key(kind, ev, mode)
        if k is None:
            continue
        v = ev["duration"] if prop is None else int(ev["props"].get(prop, "0") or 0)
        a = agg.get(k)
        if a is None:
            agg[k] = [v, 1, v]
        else:
            a[0] += v
            a[1] += 1
            if v > a[2]:
                a[2] = v
        total += 1
    return agg, total


def report(paths, kind, mode=None, value="duration", sort=None, top=100, process=None, since=None, until=None,
           workers=None):
    """Агрегаты [{key, sum, count, mean, max}] по отчёту kind; сумма/среднее/максимум — по value."""
    if kind not in REPORTS:
        raise ValueError(f"неизвестный отчёт «{kind}»: {', '.join(REPORTS)}")
    if value not in _VALUES:
        raise ValueError(f"value: {', '.join(_VALUES)}")
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise ValueError(f"нет каталога или файла ТЖ: {', '.join(missing)}")
    events = {"calls": ["CALL"], "queries": list(DB_EVENTS), "sdbl": ["SDBL"], "errors": ["EXCP", "QERR"],
              "locks": ["TLOCK"], "timeouts": ["TTIMEOUT", "TDEADLOCK"], "events": None}[kind]
    if sort is None:                                  # у ошибок и таймаутов длительности нет — по количеству
        sort = "count" if kind in ("errors", "timeouts") else "sum"
    files = list(iter_files(paths, process))
    jobs = [(f, rng, kind, mode, value, events, since, until) for f in files for rng in chunks(f)]
    agg, total = {}, 0
    if len(jobs) > 1 and (workers or os.cpu_count() or 1) > 1:
        # файлы разбираются параллельно: на журналах в гигабайты основное время — разбор, а не чтение
        with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
            parts = list(pool.map(_agg_file, jobs))
    else:
        parts = [_agg_file(j) for j in jobs]
    for part, n in parts:
        total += n
        for k, (s_, c, m) in part.items():
            a = agg.setdefault(k, [0, 0, 0])
            a[0] += s_
            a[1] += c
            a[2] = max(a[2], m)
    rows = [{"key": k, "sum": s, "count": c, "mean": s // c if c else 0, "max": m} for k, (s, c, m) in agg.items()]
    rows.sort(key=lambda r: -r[sort])
    return {"report": kind, "mode": mode, "value": value, "events": total, "groups": len(rows), "rows": rows[:top]}


def format_report(res, unit="мкс"):
    """Текст в стиле скриптов: строка метрик, затем ключ (контекст/запрос) с переносами."""
    head = (f"отчёт {res['report']}" + (f" ({res['mode']})" if res.get("mode") else "") +
            f": событий {res['events']}, групп {res['groups']}, показано {len(res['rows'])}; "
            f"{res['value']}{' в ' + unit if res['value'] in ('duration', 'cpu') else ''}")
    out = [head]
    for r in res["rows"]:
        out.append(f"\n{r['sum']}=sum, {r['count']}=count, {r['mean']}=mean, {r['max']}=max\n{r['key']}")
    return "\n".join(out)
