"""Публичная проекция отчётов: контрактные поля и безопасные текстовые значения."""

import re

PUBLIC_SUMMARY_FIELDS = frozenset(
    {
        "schema",
        "version",
        "code",
        "verdict",
        "base",
        "head",
        "modules",
        "queries",
        "findings",
        "notChecked",
        "uncovered",
        "notes",
        "infra",
        "scenarios",
        "smoke",
        "timings",
        "designer",
    }
)
PUBLIC_FINDING_FIELDS = frozenset(
    {
        "severity",
        "step",
        "rule",
        "file",
        "line",
        "column",
        "message",
        "count",
        "context",
        "object",
        "method",
        "place",
        "text",
        "sources",
    }
)
PUBLIC_SOURCE_FIELDS = frozenset({"step", "rule", "context", "message", "modes"})
PUBLIC_REASON_FIELDS = frozenset({"check", "what", "file", "line", "reason", "reasonCode", "needs"})
PUBLIC_UNCOVERED_FIELDS = frozenset({"owner", "file", "context", "needs", "reason"})
_CONNECTION = re.compile(
    r'(?:Srvr="[^"]*";Ref="[^"]*";(?:Usr="[^"]*";)?(?:Pwd="[^"]*";)?'
    r'|File="[^"]*";(?:Usr="[^"]*";)?(?:Pwd="[^"]*";)?)+',
    re.I,
)
# ключ секрета — отдельное слово или с подчёркиванием (db_password), но не хвост идентификатора (userToken)
_SECRET = re.compile(
    r'(?P<prefix>(?P<quote>["\']?)(?<![0-9A-Za-zА-Яа-яЁё])'
    r'(?:OTK_)?(?:password|pwd|пароль|token|secret)(?P=quote)\s*[=:]\s*)'
    r'(?P<value>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^;,\s}]+)',
    re.I,
)
# «/Fкаталог», «/Sсервер\база» — только похожее на строку соединения: иначе /src/... съедало бы пути файлов.
_CONNECTION_ALIAS = re.compile(r"(?<![\w/])/(?:F(?=[A-Za-z]:[\\/]|/|~|\\)|S[^\s,;]*\\)[^\s,;]+")


def public_text(value):
    if not isinstance(value, str):
        return value
    value = _CONNECTION.sub("[строка соединения скрыта]", value)
    value = _CONNECTION_ALIAS.sub("[база скрыта]", value)

    def redact(match):
        raw = match.group("value")
        if raw.startswith(('"', "'")):
            return match.group("prefix") + raw[0] + "[скрыто]" + raw[0]
        return match.group("prefix") + "[скрыто]"

    return _SECRET.sub(redact, value)


def _public_value(value):
    if isinstance(value, str):
        return public_text(value)
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    return value


def public_data(value):
    """Рекурсивно редактировать текстовые значения без изменения структуры специального отчёта."""
    return _public_value(value)


def public_summary(summary):
    """Allowlist для публичной сводки: неизвестные поля и полный текст модулей не проходят."""
    out = {key: _public_value(value) for key, value in summary.items() if key in PUBLIC_SUMMARY_FIELDS}
    out["findings"] = [
        {key: _public_value(value) for key, value in finding.items() if key in PUBLIC_FINDING_FIELDS}
        | {
            "sources": [
                {key: _public_value(value) for key, value in source.items() if key in PUBLIC_SOURCE_FIELDS}
                for source in finding.get("sources", [])
            ]
        }
        for finding in summary.get("findings", [])
    ]
    out["notChecked"] = [
        {key: _public_value(value) for key, value in item.items() if key in PUBLIC_REASON_FIELDS}
        for item in summary.get("notChecked", [])
    ]
    out["uncovered"] = [
        {key: _public_value(value) for key, value in item.items() if key in PUBLIC_UNCOVERED_FIELDS}
        for item in summary.get("uncovered", [])
    ]
    out["infra"] = [
        {key: _public_value(value) for key, value in item.items() if key in ("reasonCode", "message")}
        if isinstance(item, dict)
        else {"reasonCode": "other", "message": _public_value(str(item))}
        for item in summary.get("infra", [])
    ]
    if isinstance(out.get("scenarios"), dict):
        out["scenarios"] = {
            key: value
            for key, value in out["scenarios"].items()
            if key in ("selected", "run", "failed", "skipped", "extensions")
        }
    if isinstance(out.get("smoke"), dict):
        out["smoke"] = {
            key: value for key, value in out["smoke"].items() if key in ("selected", "opened", "skipped", "failed")
        }
    if isinstance(out.get("timings"), dict):
        out["timings"] = {
            key: value
            for key, value in out["timings"].items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    if "designer" in out and isinstance(out["designer"], dict):
        out["designer"] = {
            key: value
            for key, value in out["designer"].items()
            if key in ("ran", "modes", "baseline", "known", "fixed", "new", "closed", "control")
        }
        control = out["designer"].get("control")
        if isinstance(control, dict):
            out["designer"]["control"] = {
                key: value for key, value in control.items() if key in ("ok", "variant", "detail")
            }
    return out
