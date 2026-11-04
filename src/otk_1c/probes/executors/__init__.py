"""Исполнители проверок в базе (design.md §14): общий формат результата и разбор ошибок платформы.

Результат пробы: {"executor", "kind" (probe|query|session|extcheck), "module" (путь в плане),
"context", "outcome" (ok|compile-error|runtime-error|unknown|failed|skipped), "message", "line"}.
compile-error, unknown и failed (упавший сценарный тест или дымовой тест формы) — падения (model.FAIL);
runtime-error — предупреждение.

Исполнитель (`Executor`) — точка расширения шага run: что он проверяет (`checks` реестра checks.CHECKS), выбор
по плану с решениями реестра (`select`) и запуск (`run`). runner собирает копию ИБ и проходит `EXECUTORS` по
порядку; новая проверка в базе — модуль с исполнителем, запись в checks.CHECKS и в `EXECUTORS`.
"""
from . import com, extended_check, scenarios, yaxunit
from .base import (
    PROBE_METHOD,
    Executor,
    RunContext,
    classify,
    compile_position,
    owner_of,
    query_position,
    query_result,
    result,
)

EXECUTORS = (com.Com(), yaxunit.Probes(), yaxunit.Smoke(), extended_check.ExtCheck(), scenarios.Scenarios())

__all__ = ["EXECUTORS", "Executor", "PROBE_METHOD", "RunContext", "classify", "compile_position", "owner_of",
           "query_position", "query_result", "result"]
