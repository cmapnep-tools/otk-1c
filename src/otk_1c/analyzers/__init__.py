"""Адаптер анализаторов BSL для проверки `analyzers`: выбор `environment.analyzer`, запуск, объединение.

Анализаторы: `bslls` — BSL Language Server (bslls.py, частичная раскладка), `bsl-analyzer` (bsla.py, модель всей
конфигурации); `both` — оба, одно замечание по (модуль, строка, правило) — одна находка со списком анализаторов.
"""
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..checks import Gate
from ..probes import syntax
from ..probes.platform import find_bsl_analyzer, find_bslls, find_platform
from ..sources import revision
from ..sources.layout import Layout
from . import bsl_analyzer, bsl_ls

NAMES = ("bslls", "bsl-analyzer")
CHOICES = (*NAMES, "both")
DEFAULT = "bslls"
TITLES = {"bslls": "BSL LS", "bsl-analyzer": "bsl-analyzer"}
WHERE = {"bslls": "--bslls, environment.bslls, OTK_BSLLS, PATH или расширение VS Code «Language 1C (BSL)»",
         "bsl-analyzer": "--bsl-analyzer, environment.bslAnalyzer, OTK_BSL_ANALYZER или PATH"}


# форматы выгрузки (layout.*.format), которые анализатор понимает: bsl-analyzer 0.3.2 не читает
# метаданные EDT (.mdo) и обрывает прогон на защищённом модуле
FORMATS = ("designer-xml", "edt")
SUPPORTS = {"bslls": set(FORMATS), "bsl-analyzer": {"designer-xml"}}
# уровни раздела [analyzerSeverity]: error — правило добавить к ошибкам, warning — снять с ошибок
LEVELS = ("error", "warning")


def fail_codes(section: dict | None = None) -> tuple[dict[str, set[str]], str | None]:
    """({анализатор: правила, новое замечание которых — error}, примечание или None): встроенные
    bsl_ls.FAIL_CODES, поправленные разделом [analyzerSeverity] настроек. Ошибка в разделе — встроенные и
    примечание, прогон продолжается."""
    out = {n: set(bsl_ls.FAIL_CODES) for n in NAMES}
    try:
        for name, levels in (section or {}).items():
            if name not in NAMES:
                raise ValueError(f"{name}: неизвестный анализатор, допустимы {', '.join(NAMES)}")
            if not isinstance(levels, dict):
                raise ValueError(f"{name}: нужна таблица с ключами {', '.join(LEVELS)}")
            for level, codes in levels.items():
                if level not in LEVELS:
                    raise ValueError(f"{name}.{level}: неизвестный ключ, допустимы {', '.join(LEVELS)}")
                if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
                    raise ValueError(f"{name}.{level}: нужен список правил")
            twice = set(levels.get("error", [])) & set(levels.get("warning", []))
            if twice:
                raise ValueError(f"{name}: {', '.join(sorted(twice))} — и в error, и в warning")
    except ValueError as e:
        return out, f"[analyzerSeverity] {e} — раздел не учитывается"
    for name, levels in (section or {}).items():
        out[name] = (out[name] | set(levels.get("error", []))) - set(levels.get("warning", []))
    return out, None


def choice(env: dict, fmt: str | None = None) -> tuple[str | None, str | None]:
    """(выбор environment.analyzer для формата выгрузки fmt, предупреждение или None). Настройка — строка (для
    всех форматов) или таблица {формат: выбор}; формата нет в таблице — умолчание."""
    raw = env.get("analyzer") or DEFAULT
    if isinstance(raw, dict):
        unknown = sorted(set(raw) - set(FORMATS))
        if unknown:
            return None, f"environment.analyzer: нет формата {', '.join(unknown)} (есть: {', '.join(FORMATS)})"
        raw, where = raw.get(fmt or FORMATS[0]) or DEFAULT, f"environment.analyzer.{fmt or FORMATS[0]}"
    else:
        where = "environment.analyzer"
    value = str(raw).lower()
    if value not in CHOICES:
        return DEFAULT, f"{where} = {value!r}: допустимо {', '.join(CHOICES)} — принято {DEFAULT}"
    return value, None


def selected(env: dict, fmt: str | None = None) -> tuple[list[str], str | None]:
    """(анализаторы по environment.analyzer для формата fmt, предупреждение или None)."""
    value, warning = choice(env, fmt)
    if value is None:
        return [DEFAULT], f"{warning} — принято {DEFAULT}"
    return (list(NAMES) if value == "both" else [value]), warning


def find(name: str, env: dict) -> str | None:
    return find_bslls(env.get("bslls")) if name == "bslls" else find_bsl_analyzer(env.get("bslAnalyzer"))


def merge(by_analyzer: dict[str, list[dict]]) -> tuple[list[dict], dict[str, int]]:
    """{анализатор: находки} -> (находки без дублей по (модуль, строка, правило), {анализатор: только у него})."""
    out: list[dict] = []
    index: dict[tuple, dict] = {}
    only: dict[str, int] = {}
    for name, findings in by_analyzer.items():
        for f in findings:
            k = (f["module"], f["line"], f["check"].split(":", 1)[1])
            if k in index:
                index[k]["analyzers"].append(name)
                index[k]["severity"] = "error" if "error" in (index[k]["severity"], f["severity"]) else "warning"
                continue
            index[k] = {**f, "analyzers": [name]}
            out.append(index[k])
    if len(by_analyzer) > 1:
        for name in by_analyzer:
            only[name] = sum(1 for f in out if f["analyzers"] == [name])
    return out, only


def platform_help(env: dict) -> Path | None:
    """Каталог со справкой платформы (shcntx_ru.hbk) для BSL LS: платформы проверки (environment.platform,
    OTK_PLATFORM, самая новая установленная), иначе самой новой из найденных копий (environment.helpDir);
    None — справки нет."""
    syntax.configure(env)
    configured = env.get("platform")
    bin_dir = Path(configured) if configured else find_platform()
    if bin_dir and (Path(bin_dir) / "shcntx_ru.hbk").exists():
        return Path(bin_dir)
    found = syntax.installed_platforms()
    return Path(list(found.values())[-1]) if found else None


def run(cfg: dict, repo: str | Path, run_dir: str | Path, plan: dict, tree_root: Path, parent: str | None,
        lay: Layout, read_head: Callable[[str], str | None], ix: dict, gate: Gate) -> tuple[list[dict], dict]:
    """Проверка `bslls` выбранными анализаторами по изменённым модулям плана. Возвращает (находки, info):
    info — {"analyzer": выбор, <анализатор>: сведения о прогоне, "onlyIn": расхождения при both}."""
    env = cfg["environment"]
    names, warning = selected(env, lay.format)
    info: dict[str, Any] = {"analyzer": names[0] if len(names) == 1 else "both", "format": lay.format}
    if warning:
        info["notes"] = [warning]
    if not gate.enabled("analyzers"):
        return [], {**info, "off": True}
    unsupported = [n for n in names if lay.format not in SUPPORTS[n]]
    for n in unsupported:
        info[n] = {"skipped": f"{TITLES[n]} не поддерживает формат {lay.format}"}
    names = [n for n in names if n not in unsupported]
    if unsupported and names:
        info.setdefault("notes", []).extend(info[n]["skipped"] for n in unsupported)
    if not plan["modules"]:
        return [], {**info, "skipped": "нет модулей"}
    if not names:
        reason = info[unsupported[0]]["skipped"] + " (environment.analyzer по форматам — docs/checks.md)"
        gate.failed("analyzers", reason)
        return [], {**info, "skipped": reason}
    changed = [m for m in plan["modules"] if m.get("reason") == "changed"]
    exes = {n: find(n, env) for n in names}
    if not any(exes.values()):
        gate.lack("analyzers", " или ".join(f"{TITLES[n]} ({WHERE[n]})" for n in names))
        return [], {**info, "skipped": "анализатор не найден: " + "; ".join(WHERE[n] for n in names)}
    config = Path(repo) / ".bsl-language-server.json"
    codes, codes_note = fail_codes(cfg.get("analyzerSeverity"))
    if codes_note:
        info.setdefault("notes", []).append(codes_note)
    read_base = revision.read_base(repo, plan)
    by_analyzer = {}
    for name, exe in exes.items():
        if not exe:
            info[name] = {"skipped": f"{TITLES[name]} не найден ({WHERE[name]})"}
            info.setdefault("notes", []).append(info[name]["skipped"])
            continue
        if name == "bslls":
            found, info[name] = bsl_ls.run(
                exe, run_dir, changed, read_base, read_head, timeout=env.get("bsllsTimeout"), config=config,
                batch_bytes=int(env.get("bsllsBatchMB") or bsl_ls.BATCH_BYTES // 2**20) * 2**20, lay=lay,
                help_dir=platform_help(env))
            found = bsl_ls.drop_known(found, ix)
            if not info[name].get("help") and not info[name].get("skipped"):
                info.setdefault("notes", []).append(
                    f"BSL LS без справки платформы: правила {bsl_ls.NEEDS_HELP} не работали "
                    "(копия shcntx_ru.hbk — environment.helpDir)")
        else:
            found, info[name] = bsl_analyzer.run(
                exe, run_dir, Path(run_dir) / "cache", Path(tree_root) / plan["source"], changed, read_base,
                read_head, env, repo, lay=lay, extension=cfg["project"].get("extension") or None,
                parent=parent, excluded=bsl_ls.excluded_by(config))
        for f in found:
            f["severity"] = "error" if f["check"].split(":", 1)[1] in codes[name] else "warning"
        by_analyzer[name] = found
    findings, only = merge(by_analyzer)
    if len(by_analyzer) == 1:
        for f in findings:
            f.pop("analyzers")
    if only:
        info["onlyIn"] = only
    errors = [f"{TITLES[n]}: {info[n]['error']}" for n in by_analyzer if info[n].get("error")]
    if errors and len(errors) == len(by_analyzer):
        info["error"] = "; ".join(errors)
    elif errors:
        info.setdefault("notes", []).extend(f"{e} — не выполнен" for e in errors)
    return findings, info

