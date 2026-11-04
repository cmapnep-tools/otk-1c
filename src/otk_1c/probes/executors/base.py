"""Общее для исполнителей: формат результата, разбор сообщений платформы, контекст запуска, интерфейс."""
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ...checks import Gate
from ...lang import platform_texts
from ...sources import layout
from .. import build, edt
from ..platform import Platform

PROBE_METHOD = "__ОТКПроба__"
_POS = re.compile(r"\{([^{}()]+?)\((\d+),(\d+)\)\}:\s*([^\r\n]+)")
_QUERY_POS = re.compile(r"\{\((\d+),\s*(\d+)\)\}:\s*([^\r\n]+)")


def result(executor: str, kind: str, entry: dict | None, context: str | None, outcome: str, message: str = "",
           line: int | None = None) -> dict:
    return {"executor": executor, "kind": kind, "module": entry.get("path") if entry else None,
            "owner": entry.get("owner") if entry else None, "context": context, "outcome": outcome,
            "message": message, "line": line}


def compile_position(text: str | None) -> tuple[str, int, int, str] | None:
    """(имя модуля платформы, строка, колонка, сообщение) первой ошибки с позицией, или None."""
    m = _POS.search(text or "")
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2)), int(m.group(3)), m.group(4).strip()


def owner_of(platform_module: str) -> str:
    """'ОбщийМодуль.X.Модуль' -> 'ОбщийМодуль.X'; 'Документ.Д.Форма.Ф.Форма' -> 'Документ.Д.Форма.Ф'."""
    parts = platform_module.split(".")
    kind = platform_texts()["modules"].get(parts[-1])
    if len(parts) > 1 and kind and kind not in layout.ROOT_KINDS:
        return ".".join(parts[:-1])
    return platform_module


def classify(text: str | None, entry: dict | None = None) -> tuple[str, int | None, str]:
    """Исход по тексту исключения: ok (метод-проба не найден), compile-error (позиция в модуле),
    runtime-error или unknown. Признаки ошибки компиляции — тексты интерфейса платформы из словаря языка."""
    text = text or ""
    words = platform_texts()
    compiling = any(w.lower() in text.lower() for w in words["compileInText"])
    if PROBE_METHOD in text and not compiling:
        return "ok", None, ""
    pos = compile_position(text)
    if pos and (compiling or any(w in pos[3] for w in words["compileInMessage"])):
        return "compile-error", pos[1], f"{pos[0]}({pos[1]},{pos[2]}): {pos[3]}"
    if pos:
        return "runtime-error", pos[1], f"{pos[0]}({pos[1]},{pos[2]}): {pos[3]}"
    return "unknown", None, " ".join(text.split())[:500]


def query_position(text: str | None) -> tuple[int, int, str] | None:
    m = _QUERY_POS.search(text or "")
    return (int(m.group(1)), int(m.group(2)), m.group(3).strip()) if m else None


_FOREIGN_TT = re.compile(r'Таблица не найдена "([^".]+)"|Table not found "([^".]+)"')
_FIELD = re.compile(r'Поле не найдено "([^"]+)"|Field not found "([^"]+)"')
_QW = r"[\wА-Яа-яЁё]"
_SOURCE = re.compile(rf"(?<!{_QW})(?:FROM|ИЗ|JOIN|СОЕДИНЕНИЕ)\s+(&?{_QW}+(?:\.{_QW}+)*)(?:\s+(?:AS|КАК)\s+({_QW}+))?",
                     re.I)
_INTO = re.compile(rf"(?<!{_QW})(?:INTO|ПОМЕСТИТЬ)\s+({_QW}+)", re.I)


def _unknown_columns(query_text: str, field: str) -> bool:
    """Поле может браться из источника, колонок которого разбор не знает: параметр-таблица (`ИЗ &Таблица`) или
    временная таблица другого запроса (и временные таблицы пакета, построенные из них). Если в тексте такой
    источник есть, ошибкой остаётся только поле, псевдоним которого ведёт к таблице метаданных (`Справочник.X`)."""
    created = {n.lower() for n in _INTO.findall(query_text)}
    sources = _SOURCE.findall(query_text)
    if not any(n.startswith("&") or ("." not in n and n.lower() not in created) for n, _ in sources):
        return False
    if "." not in field:
        return True
    alias = field.split(".", 1)[0].lower()
    metadata = {(a or n).lower() for n, a in sources if "." in n and not n.startswith("&")}
    return alias not in metadata


def query_result(executor: str, q: dict, ctx: str | None, text: str | None) -> dict:
    """Результат разбора запроса с ошибкой. Не проверено (а не ошибка): таблица без точки в имени, которой нет в
    тексте, — временная таблица другого запроса (менеджер временных таблиц); поле источника, колонок которого
    разбор не знает (параметр-таблица, ВТ другого запроса)."""
    pos = query_position(text)
    entry = {"path": q["module"], "owner": q["id"]}
    line = q["line"] + pos[0] - 1 if pos else q["line"]
    message = pos[2] if pos else " ".join((text or "").split())[:300]
    tt = _FOREIGN_TT.search(message)
    if tt:
        return {**result(executor, "query", entry, ctx, "skipped",
                         f"временная таблица другого запроса «{tt.group(1) or tt.group(2)}» — без неё не разобрать",
                         line), "query": q["id"]}
    fld = _FIELD.search(message)
    if fld and q.get("text") and _unknown_columns(q["text"], fld.group(1) or fld.group(2)):
        return {**result(executor, "query", entry, ctx, "skipped",
                         f"поле «{fld.group(1) or fld.group(2)}» из параметра-таблицы или ВТ другого запроса — "
                         "колонки при разборе неизвестны", line), "query": q["id"]}
    return {**result(executor, "query", entry, ctx, "compile-error", message, line), "query": q["id"]}


@dataclass
class RunContext:
    """Шаг run для исполнителей: план, настройки, копия ИБ и решения реестра проверок одного прогона.

    have — зависимости проверок ({"ib", "yaxunit", "com"}: есть ли), их дополняет select исполнителя;
    handed — элементы, переданные исполнителю по имени (обход: COM недоступен -> YAxUnit); results, notes,
    timings — то, что попадёт в run.json."""
    repo: Path
    plan: dict
    cfg: dict
    run_dir: Path
    tree_root: Path
    config_root: Path
    platform: Platform
    gate: Gate
    install: Callable          # install(renames) -> строка состояния инструментов (runner.install_tools)
    session: Any = None        # userbase.Session — проверка в рабочей базе пользователя
    log: Callable = print
    have: dict = field(default_factory=dict)
    handed: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    tools_ready: bool = False
    tools_conflict: str = ""   # конфликт имён YAxUnit не устранён — следующим запускам YAxUnit не повторять
    covered: set = field(default_factory=set)   # модули, целиком проверенные extcheck во всех его режимах
    designer_dump: str | None = None  # исходники EDT: выгрузка экспорта, загруженная сборкой (build: результат "dump")

    @property
    def env(self) -> dict:
        return self.cfg["environment"]

    def designer_root(self, names: list[str] | None = None) -> Path:
        """config_root в формате Конфигуратора: исходники EDT — выгрузка экспорта 1cedtcli из сборки; сборка не
        экспортировала — частичная выгрузка ИБ объектов names (строки -listFile), без names — экспорт сейчас
        (кеш по отпечатку проекта). InfraError — выгрузка или экспорт не удались."""
        if layout.detect(self.config_root) is not layout.EDT:
            return self.config_root
        if not self.designer_dump and names is not None:
            target = Path(self.run_dir) / edt.IB_DIR / "extcheck"
            sec = edt.from_ib(self.platform, self.config_root, target, names, label="edt-ib-extcheck")
            self.log(f"выгрузка ИБ для расширенной проверки (без экспорта EDT): объектов {len(names)}, {sec} c")
            self.timings["ibDump"] = round(self.timings.get("ibDump", 0) + sec, 1)
            return target
        if not self.designer_dump:
            dump, sec = build.loadable(self.platform, self.run_dir, self.config_root)
            if sec:
                self.log(f"экспорт EDT для расширенной проверки: {sec} c")
                self.timings["export"] = round(self.timings.get("export", 0) + sec, 1)
            self.designer_dump = str(dump)
        return Path(self.designer_dump)

    @property
    def modules(self) -> list:
        return self.plan["modules"]

    def hand(self, executor: str, key: str, items: list) -> None:
        """Передать элементы исполнителю executor (он выполнит их, даже если сам ничего не выбрал)."""
        if items:
            self.handed.setdefault(executor, {}).setdefault(key, []).extend(items)

    def ensure_tools(self, renames: tuple | list = ()) -> None:
        """YAxUnit и расширение проб — при первой надобности: COM и расширенной проверке они не нужны."""
        if self.tools_ready and not renames:
            return
        t = time.time()
        self.log(f"инструменты: {self.install(renames)}")
        self.timings["tools"] = round(self.timings.get("tools", 0) + time.time() - t, 1)
        self.tools_ready = True


class Executor(Protocol):
    name: str                  # ключ времени в run.json timings
    checks: tuple              # проверки реестра checks.CHECKS, которые он выполняет

    def select(self, ctx: RunContext) -> Any:
        """Что проверять (решения реестра — ctx.gate.ready); пусто — не запускать, если ничего не передано.
        Может сразу дописать в ctx.results «пропущено» по выбранному (COM: сервер без внешнего соединения без
        YAxUnit) и передать элементы другому исполнителю (ctx.hand)."""

    def run(self, ctx: RunContext, items: Any) -> tuple:
        """(результаты, примечание или пусто) по выбранному в select и переданному в ctx.handed[name]."""
