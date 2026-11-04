"""Реестр проверок (спецификация check-registry): что каждой нужно, что теряется без неё, включение `[checks]`.

`[checks] <имя> = "on" | "auto" | "off"` (по умолчанию auto). Зависимости нет: on — сбой инфраструктуры (код 2) с
названием недостающего; auto — «не проверено» с причиной в сводке, JUnit и Problems; off — не выполняется, в
сводке одна строка «выключено настройкой».
"""
from dataclasses import dataclass
from typing import Any

MODES = ("on", "auto", "off")


@dataclass(frozen=True)
class Check:
    name: str
    title: str
    needs: tuple            # зависимости; вложенный кортеж — достаточно любой из них
    loses: str              # что теряется без проверки


CHECKS = (
    Check("metadata", "ссылки на метаданные, формы, обработчики, экспортные методы", (),
          "ошибки ссылок на метаданные, формы и экспортные методы; пробы зависимых модулей"),
    Check("analyzers", "новые замечания анализатора BSL", ("analyzer",), "замечания анализатора по правке"),
    Check("platform-api", "методы и типы платформы", ("help",),
          "выдуманные и слишком новые методы и типы платформы"),
    Check("probes-server", "компиляция на сервере и во внешнем соединении", ("ib", ("com", "yaxunit")),
          "ошибки компиляции серверных модулей"),
    Check("probes-client", "компиляция в тонком клиенте", ("ib", "yaxunit"), "ошибки компиляции клиентских модулей"),
    Check("queries", "разбор запросов платформой", ("ib", ("com", "yaxunit")), "ошибки в текстах запросов"),
    Check("extcheck", "расширенная проверка изменённых модулей", ("ib",), "расширенная проверка Конфигуратором"),
    Check("scenarios", "сценарные тесты по изменённым методам", ("ib", "yaxunit"), "логика изменённых методов (тесты)"),
    Check("smoke", "открытие изменённых форм", ("ib", "yaxunit"), "ошибки при открытии форм"),
)
BY_NAME = {c.name: c for c in CHECKS}

DEPS = {
    "ib": "копия ИБ (--ib или --connection; environment.ib или connection)",
    "yaxunit": "YAxUnit (--yaxunit; environment.yaxunit)",
    "com": "COM-соединение (Windows, pywin32)",
    "analyzer": "анализатор BSL по environment.analyzer: BSL LS (--bslls, environment.bslls, OTK_BSLLS, PATH "
                "или расширение VS Code «Language 1C (BSL)») "
                "или bsl-analyzer (--bsl-analyzer, environment.bslAnalyzer, OTK_BSL_ANALYZER или PATH)",
    "edtcli": "1cedtcli для исходников EDT (--edtcli, environment.edtcli, OTK_EDTCLI, установка 1C:EDT или PATH)",
    "help": "справка установленной платформы (shcntx_ru.hbk)",
}
# зависимость doctor и DEPS -> код причины (gate/reasons.CODES), когда её нет
DEP_CODES = {"python": "no-python", "git": "no-git", "platform": "no-platform", "ibcmd": "no-ibcmd", "ib": "no-ib",
             "yaxunit": "no-yaxunit", "display": "no-display", "com": "no-com", "bslls": "no-analyzer",
             "bsl-analyzer": "no-analyzer", "analyzer": "no-analyzer", "edtcli": "no-edtcli", "help": "no-help",
             "availability": "no-availability"}

def modes(cfg: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """({проверка: режим}, предупреждения о настройках)."""
    warnings = []
    raw = dict(cfg.get("checks", {}))
    for name in raw:
        if name not in BY_NAME:
            warnings.append(f"[checks] {name}: такой проверки нет (есть: {', '.join(BY_NAME)})")
    out = {}
    for c in CHECKS:
        mode = str(raw.get(c.name, "auto")).lower()
        if mode not in MODES:
            warnings.append(f"[checks] {c.name} = {raw[c.name]!r}: допустимо on, auto, off — принято auto")
            mode = "auto"
        out[c.name] = mode
    return out, warnings


def missing_keys(name: str, have: dict[str, Any]) -> list[tuple[str, ...]]:
    """Недостающие зависимости проверки ключами DEPS: каждая — кортеж альтернатив (достаточно любой)."""
    out = []
    for need in BY_NAME[name].needs:
        alternatives = need if isinstance(need, tuple) else (need,)
        if not any(have.get(a) for a in alternatives):
            out.append(alternatives)
    return out


def missing(name: str, have: dict[str, Any]) -> list[str]:
    """Недостающие зависимости проверки: have — {зависимость: есть ли}; неизвестная — считается отсутствующей."""
    return [" или ".join(DEPS[a] for a in alts) for alts in missing_keys(name, have)]


class Gate:
    """Решения по проверкам одного прогона: что выполнять и что сказать о пропущенном."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.mode, self.warnings = modes(cfg)
        # сбой инфраструктуры: {"message", "reasonCode"} (код None — по тексту, gate/reasons.entry)
        self.infra: list[dict[str, Any]] = []
        self.not_checked: list[dict[str, Any]] = []
        self.blocked: set[str] = set()
        self._taken = (0, 0)

    def enabled(self, name: str) -> bool:
        return self.mode[name] != "off" and name not in self.blocked

    def block(self, name: str, reason: str, code: str | None = None) -> None:
        """Проверку в этом прогоне выполнять нельзя (reason — почему): решение как у failed, дальше — выключена."""
        if self.enabled(name):
            self.failed(name, reason, code)
            self.blocked.add(name)

    def lack(self, name: str, what: str, code: str | None = None) -> None:
        """Проверка нужна, но зависимости нет: on — сбой инфраструктуры, auto — «не проверено»."""
        self.failed(name, f"не найдено: {what}", code)

    def failed(self, name: str, reason: str, code: str | None = None) -> None:
        """Проверка нужна, но не выполнилась не из-за кода (reason — почему, code — код причины, если известен):
        on — сбой инфраструктуры, auto — «не проверено»."""
        if self.mode[name] == "off":
            return
        check = BY_NAME[name]
        if self.mode[name] == "on":
            self.infra.append({"message": f"[checks] {name} = \"on\", но {reason}", "reasonCode": code})
        elif not any(i["check"] == name for i in self.not_checked):
            self.not_checked.append({"check": name, "what": f"проверка «{check.title}»", "file": None,
                                     "line": None, "reason": f"{reason} — теряется: {check.loses}",
                                     **({"reasonCode": code} if code else {})})

    def ready(self, name: str, have: dict[str, Any]) -> bool:
        """Выполнять ли проверку: включена и зависимости есть; иначе — lack по недостающему."""
        if not self.enabled(name):
            return False
        lacking = missing_keys(name, have)
        if lacking:
            self.lack(name, ", ".join(" или ".join(DEPS[a] for a in alts) for alts in lacking),
                      DEP_CODES.get(lacking[0][0]))
            return False
        return True

    def take(self) -> dict[str, list]:
        """Решения с прошлого вызова — в файл шага, где приняты: {"notChecked": [...], "infra": [...]}."""
        nc, inf = self._taken
        self._taken = (len(self.not_checked), len(self.infra))
        return {"notChecked": self.not_checked[nc:], "infra": self.infra[inf:]}

    def notes(self) -> list[str]:
        off = [n for n, m in self.mode.items() if m == "off"]
        return self.warnings + ([f"выключено настройкой: {', '.join(off)}"] if off else [])
