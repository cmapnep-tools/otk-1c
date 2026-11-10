"""Типизированные причины «не проверено» и сбоев инфраструктуры (`gate --json`, `doctor --json`).

Коды стабильны: агент по `reasonCode` решает, что чинить; список и тексты — docs/agents.md. Код задаёт место сбоя
(`InfraError(code=)`, `Gate.failed(code=)`, зависимость — checks.DEP_CODES); без него — по тексту (classify): это
сообщения внешних программ (платформа, Конфигуратор, ibcmd). Порядок образцов важен: первым совпавшим определяется
код («не выполнено: COM-соединение…» — сбой пробы, а не нет COM).
"""
import re
from pathlib import Path
from typing import Any

from .. import __version__
from ..public import public_data, public_summary, public_text

# код: что сделать (remediation)
CODES = {
    "ib-busy": "закройте Конфигуратор и сеансы 1С:Предприятия с этой базой и повторите gate",
    "ib-auth": "задайте пользователя ИБ (--user, environment.user) и пароль (переменная OTK_PASSWORD)",
    "ib-invalid": "укажите каталог файловой ИБ (с 1Cv8.1CD) или пустой каталог — ОТК создаст копию сам",
    "no-ib": "укажите свою копию ИБ (--ib, environment.ib в otk.local.toml) или --connection; без неё — "
             "gate --static-only",
    "no-platform": "установите платформу 1С или укажите каталог bin: --platform, environment.platform",
    "no-ibcmd": "установите компоненты сервера 1С:Предприятия или задайте environment.ibTool = designer",
    "no-license": "нужна лицензия 1С на этой машине; без неё — только статика: gate --static-only",
    "no-yaxunit": "укажите YAxUnit (.cfe): --yaxunit, environment.yaxunit",
    "no-com": "COM-соединение — только Windows с pywin32 (pip install \"otk-1c[com]\"); иначе пробы — через "
              "YAxUnit (--yaxunit)",
    "no-display": "Linux: установите Xvfb (apt install xvfb) или задайте DISPLAY",
    "no-analyzer": "установите BSL LS или bsl-analyzer (--bslls, --bsl-analyzer) либо выключите проверку: "
                   "checks.analyzers = off",
    "no-edtcli": "установите 1C:EDT или укажите 1cedtcli: --edtcli, environment.edtcli",
    "no-help": "нужна справка установленной платформы (shcntx_ru.hbk): --platform на полную установку",
    "no-availability": "постройте таблицу доступности для этой платформы: otk availability build",
    "no-git": "установите git или проверяйте каталоги без git: gate --dir (и --base-dir)",
    "no-python": "нужен Python 3.11 или новее",
    "no-source": "укажите каталог выгрузки с файлом конфигурации: --dir или --source (project.source)",
    "no-parent": "укажите выгрузку основной конфигурации расширения: --parent (project.parent)",
    "no-plan": "сначала otk collect или otk gate — они пишут план otk.json",
    "timeout": "платформа не завершилась вовремя: проверьте, не ждёт ли она диалога (лицензия, обновление), "
               "и повторите; тайм-ауты — в настройках, названных в сообщении",
    "platform-failed": "Конфигуратор или ibcmd завершился ошибкой (текст — в сообщении): проверьте копию ИБ и "
                       "выгрузку и повторите; править код по этой причине бесполезно",
    "restore-failed": "ОТК не вернула базу к виду до проверки (рабочая база, откат Platform Tools): верните "
                      "расширения вручную из сохранённых .cfe (пути — в сообщении), потом повторите",
    "edt-failed": "экспорт EDT завершился ошибкой (текст — в сообщении): проверьте проект в 1C:EDT",
    "config-invalid": "исправьте настройку, названную в сообщении (otk.toml, otk.local.toml или ключ)",
    "probe-failed": "исполнитель проб не запустился (COM, сеанс YAxUnit): повторите прогон; повторяется — "
                    "otk doctor",
    "query-incomplete": "текст запроса собран из переменных частей — проверяется только исполнением "
                        "(сценарные тесты или проверка вручную)",
    "format-unsupported": "формат файлов выгрузки новее платформы проверки: проверьте платформой новее или возьмите "
                          "выгрузку этой платформы (--platform, environment.platform)",
    "internal": "сбой самой ОТК, а не проверяемого кода: повторите; повторяется — сообщите об ошибке ОТК с текстом "
                "сообщения и журналом (stderr)",
    "other": "причина — в тексте сообщения",
}

# (код, образец по тексту в нижнем регистре): первый совпавший
_PATTERNS = [
    ("ib-busy", r"занята|ошибка блокировки информационной базы"),
    ("timeout", r"не завершил(ся|ась) за|тайм-аут"),
    ("no-license", r"лиценз"),
    ("ib-auth", r"запрашивает имя пользователя"),
    ("probe-failed", r"^не выполнено:"),
    ("no-ibcmd", r"ibcmd в .* нет|нет ibcmd|ibcmd .*не найден"),
    ("no-platform", r"платформа 1с не найдена"),
    ("ib-invalid", r"файловой иб \(1cv8\.1cd\) в нём нет|копии иб .* нет: для проверки расширения"),
    ("no-ib", r"копия иб \(--ib|не задана копия иб|нет копии иб"),
    ("no-yaxunit", r"yaxunit \(--yaxunit|не задан yaxunit"),
    ("no-com", r"com-соединение \(windows|com недоступен"),
    ("no-display", r"нет xvfb"),
    ("no-analyzer", r"анализатор bsl по environment\.analyzer|bsl ls не найден|bsl-analyzer не найден"),
    ("no-edtcli", r"1cedtcli"),
    ("no-help", r"справк[аи] (установленной )?платформы"),
    ("no-plan", r"нет плана"),
    ("no-parent", r"^parent = |основной конфигурации.*не найден"),
    ("no-source", r"нет файла конфигурации|не выгрузка конфигурации"),
    ("edt-failed", r"экспорт edt"),
    ("query-incomplete", r"переменными частями"),
    ("format-unsupported", r"неизвестная версия формата|формат выгрузки \d+(\.\d+)* новее"),
    ("config-invalid", r"допустимо|неизвестные ключи|environment\.\w+ =|несовместим|ошибка синтаксиса toml"),
    ("platform-failed", r"rc=|не удал(ся|ась|ось)|не создаётся|не загрузился|не восстановлен|конфигуратор"),
]
_COMPILED = [(code, re.compile(p)) for code, p in _PATTERNS]

def classify(text: str | None) -> str:
    """Код причины по её тексту; не распознана — other."""
    low = " ".join(str(text or "").split()).lower()
    return next((code for code, rx in _COMPILED if rx.search(low)), "other")


def remediation(code: str) -> str:
    return CODES.get(code, CODES["other"])


def entry(value: str | dict[str, Any]) -> dict[str, Any]:
    """Сбой инфраструктуры для summary.json: {reasonCode, message}; код места сбоя или по тексту."""
    value = {"message": value} if isinstance(value, str) else value
    message = public_text(value["message"])
    return {"reasonCode": value.get("reasonCode") or classify(value["message"]), "message": message}


def item(text: str | None, status: str, code: str | None = None, **extra) -> dict:
    """Причина для JSON: {status, reasonCode, remediation, message, ...extra}; код — по тексту, если не задан."""
    code = code or classify(text)
    return {"status": status, "reasonCode": code, "remediation": remediation(code), "message": public_text(text or ""),
            **extra}


def gate_status(summary: dict) -> str:
    """ok, nothing (нечего проверять), errors, infra (сбой инфраструктуры), not_checked (ничего не проверено)."""
    if summary["code"] == 1:
        return "errors"
    if summary["code"] == 2:
        return "infra" if summary.get("infra") else "not-checked"
    return "nothing" if summary["verdict"] == "нечего проверять" else "ok"


def command_result(command: str, code: int, result=None, error=None) -> dict:
    """Общий конверт результата CLI-команды со стабильным именем схемы и кодом выхода."""
    status = {0: "ok", 1: "errors", 2: "infra"}.get(code, "other")
    return {"schema": f"otk.{command}-result.v1", "version": __version__, "code": code, "status": status,
            "result": public_data(result), "error": public_data(error)}


def command_error(command: str, text: str, code: str | None = None) -> dict:
    """Общий результат инфраструктурной ошибки CLI-команды."""
    return command_result(command, 2, error=item(text, "infra", code))


def report_result(summary: dict, run_dir) -> dict:
    """Итог report --json (schema/report-result.schema.json) со сводкой, собранной из файлов прогона."""
    return {"schema": "otk.report-result.v1", "version": __version__, "code": summary["code"],
            "status": gate_status(summary), "runDir": str(run_dir), "summaryPath": str(Path(run_dir) / "summary.json"),
            "summary": public_summary(summary), "error": None}


def report_error(text: str, code: str | None = None) -> dict:
    """Итог report --json, когда сводку собрать нельзя (нет плана, настройка, сбой ОТК): код 2."""
    return {"schema": "otk.report-result.v1", "version": __version__, "code": 2, "status": "infra",
            "runDir": None, "summaryPath": None, "summary": None, "error": item(text, "infra", code)}


def gate_result(summary: dict, run_dir) -> dict:
    """Итог gate --json (schema/gate-result.schema.json) по сводке прогона."""
    infra = [entry(x) for x in summary.get("infra", [])]
    summary = public_summary({**summary, "infra": infra})
    not_checked = [item(i.get("reason"), "not-checked", i.get("reasonCode"), what=i["what"], file=i.get("file"),
                        line=i.get("line"), **{k: i[k] for k in ("check", "needs") if i.get(k)})
                   for i in summary["notChecked"]]
    return {"schema": "otk.gate-result.v1", "version": __version__, "code": summary["code"],
            "status": gate_status(summary), "verdict": summary["verdict"], "runDir": str(run_dir),
            "summaryPath": str(Path(run_dir) / "summary.json"), "findings": summary["findings"],
            "notChecked": not_checked, "infra": [item(x["message"], "infra", x["reasonCode"]) for x in infra],
            "uncovered": summary["uncovered"], "notes": summary["notes"]}


def gate_error(text: str, code: str | None = None) -> dict:
    """Итог gate --json, когда прогон оборвался до сводки (нет выгрузки, git, настройка; code=internal —
    исключение в самой ОТК): код 2."""
    return {"schema": "otk.gate-result.v1", "version": __version__, "code": 2, "status": "infra",
            "verdict": "сбой инфраструктуры", "runDir": None, "summaryPath": None, "findings": [], "notChecked": [],
            "infra": [item(text, "infra", code)], "uncovered": [], "notes": []}


def doctor_error(text: str, code: str | None = None) -> dict:
    """Итог doctor --json, когда проверка не дошла до окружения (битый otk.toml, сбой ОТК): код 2."""
    return {"schema": "otk.doctor-result.v1", "version": __version__, "code": 2,
            "project": {"root": None, "found": False}, "environment": [], "checks": [], "warnings": [],
            "staticOnly": False, "error": item(text, "infra", code)}


def doctor_result(res: dict, project: dict) -> dict:
    """Итог doctor --json (schema/doctor-result.schema.json): doctor.result + проект."""
    return {"schema": "otk.doctor-result.v1", "version": __version__, "project": project, **res}
