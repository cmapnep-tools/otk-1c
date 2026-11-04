"""Модель данных шагов (design.md refactor-layers §2–§3): исходы проб и формы записей файлов прогона.

Словари остаются словарями (JSON): типы ниже — для чтения кода и подсказок IDE, рантайм-проверок нет.
Схемы файлов — `schema/{plan,static,run,summary}.schema.json`.
"""
import hashlib
import json
from collections.abc import Callable
from enum import StrEnum
from typing import Any, NotRequired, TypedDict

from . import __version__

# журнал хода прогона: строка -> вывод (print, сбор в список, тишина)
Log = Callable[[str], object]

PLAN_SCHEMA = "otk.plan.v1"
INTERNAL = "внутренний файл ОТК, не контракт: формат меняется без предупреждения, не править вручную"


def header(name: str, internal: bool = False) -> dict[str, str]:
    """Начало файла JSON: schema otk.<name>.v1 и версия ОТК; внутренний файл (не контракт) — с $comment."""
    return {**({"$comment": INTERNAL} if internal else {}), "schema": f"otk.{name}.v1", "version": __version__}


class Outcome(StrEnum):
    """Исход пробы (результат исполнителя)."""
    OK = "ok"
    COMPILE_ERROR = "compile-error"
    RUNTIME_ERROR = "runtime-error"     # предупреждение: код компилируется, упал при выполнении пробы
    UNKNOWN = "unknown"                 # платформа не ответила понятно — считается провалом
    SKIPPED = "skipped"
    FAILED = "failed"                   # упал сценарный тест или дымовой тест формы


# единственное определение провала проб: код выхода run, gate, хуков и ошибки сводки
FAIL = frozenset({Outcome.COMPILE_ERROR, Outcome.UNKNOWN, Outcome.FAILED})


class ModuleEntry(TypedDict):
    """Модуль плана (otk.json `modules`) или зависимый модуль (static.json `dependents`)."""
    path: str
    status: str
    kind: str
    owner: str
    reason: str                         # changed | dependent
    contexts: list
    preprocessorConditions: list
    preprocessorContexts: list
    needsPlainServerProbe: bool
    changedMethods: list
    removedMethods: list
    probe: dict
    uncovered: list                     # [{context, needs, reason}] — не покрыто этим прогоном
    dependentLines: NotRequired[list]   # только зависимые: строки вызовов изменённых методов


class Query(TypedDict):
    id: str
    module: str
    line: int
    complete: bool
    text: str
    assembled: NotRequired[bool]
    unchanged: NotRequired[bool]
    reason: NotRequired[str]


class Plan(TypedDict):
    """План проверки otk.json: пишет collect, после него не меняется."""
    version: int
    base: str | None
    baseSha: str | None
    baseDir: NotRequired[str | None]    # без git: выгрузка «было»
    headDir: NotRequired[str | None]    # без git: проверяемая выгрузка
    head: str
    headSha: str | None
    sourceFormat: str
    source: str
    scriptVariant: str
    contextsAvailable: list
    modules: list[ModuleEntry]
    removedModules: list
    metadata: list
    queries: list[Query]
    scenarios: list
    scenarioExtensions: NotRequired[list]
    allScenarios: NotRequired[bool]
    forms: NotRequired[list]
    smoke: NotRequired[list]
    notes: NotRequired[list]            # решения сценария до шагов: настройки [checks], расширение без parent


class StaticFinding(TypedDict):
    check: str
    severity: str                       # error | warning
    module: str
    line: int | None
    message: str


class ProbeResult(TypedDict):
    executor: str
    kind: str
    module: str | None
    owner: str | None
    context: str | None
    outcome: str                        # Outcome
    message: str | None
    line: int | None


class Finding(TypedDict):
    """Находка сводки (summary.json `findings`)."""
    severity: str                       # error | warning
    step: str
    code: str
    file: str | None
    line: int
    column: int
    message: str
    count: NotRequired[int]


def plan_id(plan: Plan | dict[str, Any]) -> str:
    """Отпечаток плана: файлы шагов хранят его в `plan` — файл шага от другого плана не учитывается."""
    text = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def modules(plan: Plan | dict[str, Any], static: dict[str, Any] | None = None) -> list:
    """Модули к проверке в базе и в сводке: план и зависимые модули из static.json (без повторов)."""
    known = {m["path"] for m in plan["modules"]}
    extra = [m for m in (static or {}).get("dependents", []) if m["path"] not in known]
    return plan["modules"] + extra


def with_dependents(plan: Plan | dict[str, Any], static: dict[str, Any] | None = None) -> dict[str, Any]:
    """Копия плана, в которой modules — с зависимыми модулями static.json; сам план не меняется."""
    return {**plan, "modules": modules(plan, static)}
