"""Настройки проекта: otk.toml в корне репозитория + otk.local.toml (вне git).

Приоритет: аргументы командной строки > переменные OTK_* > otk.local.toml > otk.toml > значения по умолчанию.
Переменные читаются только здесь (ENV_VARS): модули берут значения из настроек.
"""
import copy
import os
import tomllib
from argparse import Namespace
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import InfraError
from .sources import layout

# настройки прогона: разделы otk.toml (project, environment, checks, …) -> ключ -> значение
Config = dict[str, Any]

DEFAULTS: Config = {
    "project": {"source": "src/cf"},
    "environment": {
        "contexts": ["Server", "ThinClient", "ExternalConnection"],
        "ordinaryApplication": False,
        "webClient": False,
        "runDir": "",
        "helpAuto": True,
    },
    "report": {"open": ""},
}

# переменные окружения -> environment.<ключ>: сильнее файлов настроек, слабее аргументов командной строки
ENV_VARS = {"OTK_IB": "ib", "OTK_USER": "user", "OTK_PASSWORD": "password", "OTK_PLATFORM": "platform",
            "OTK_YAXUNIT": "yaxunit", "OTK_BSLLS": "bslls", "OTK_BSL_ANALYZER": "bslAnalyzer", "OTK_EDTCLI": "edtcli",
            "OTK_COM": "comConnector", "OTK_IB_TOOL": "ibTool", "OTK_RUN_DIR": "runDir",
            "OTK_HIDDEN_DESKTOP": "hiddenDesktop"}
BOOL_VARS = {"hiddenDesktop"}

# известные разделы и ключи: остальное — предупреждение (опечатка не должна выключать настройку молча);
# None — ключи раздела проверяет его потребитель (checks.modes, analyzers.fail_codes, platform_api)
KNOWN: dict[str, set[str] | None] = {
    "project": {"source", "parent", "extension"},
    "environment": {"ib", "user", "password", "connection", "keepTools", "loadProject", "platform", "comConnector",
                    "bslls", "bsllsTimeout", "bsllsBatchMB", "yaxunit", "ibTool", "analyzer", "bslAnalyzer",
                    "bslAnalyzerTimeout", "edtcli", "edtTimeout", "hiddenDesktop", "contexts", "ordinaryApplication",
                    "webClient", "minPlatform", "compatWarnings", "executors", "runDir", "helpDir", "helpAuto",
                    "lockTimeout"},
    "scenarios": {"path"}, "checks": None, "platformNames": None, "analyzerSeverity": None,
    "smoke": {"exclude", "timeout", "severity"}, "queries": {"resolvers"}, "scaffold": {"extension", "draft"},
    "hook": {"base", "mode", "fast", "infraFail"}, "report": {"open"},
    "designerCheck": {"modes", "baseline"},
}


def env_values(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Ключи [environment] из непустых переменных OTK_* (ENV_VARS); 0, false, no — ложь для логических."""
    environ = os.environ if environ is None else environ
    out: dict[str, Any] = {}
    for var, key in ENV_VARS.items():
        value = environ.get(var)
        if value:
            out[key] = value.strip().lower() not in ("0", "false", "no") if key in BOOL_VARS else value
    return out


def unknown_keys(cfg: Config) -> list[str]:
    """Неизвестные разделы и ключи настроек (раздел.ключ) — для предупреждения в doctor и сводке."""
    out = []
    for section, value in cfg.items():
        if section not in KNOWN:
            out.append(f"[{section}]")
        elif (known := KNOWN[section]) is not None and isinstance(value, dict):
            out += [f"{section}.{k}" for k in value if k not in known and not k.endswith("Detected")]
    return out


def warnings(cfg: Config) -> list[str]:
    bad = unknown_keys(cfg)
    return [f"неизвестные ключи настроек (опечатка?): {', '.join(bad)} — не учитываются"] if bad else []


def _merge(base: Config, extra: Config) -> Config:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def parent_dir(repo: str | Path, cfg: Config, override: str | None = None) -> Path | None:
    """Каталог основной конфигурации (project.parent или --parent) или None. Нет файла конфигурации — InfraError
    с понятной причиной: у адаптеров выгрузка типовой часто лежит вне git (ссылка в основном клоне), и в
    рабочем дереве git worktree её нет."""
    parent = override or cfg["project"].get("parent")
    if not parent:
        return None
    # из otk.toml — от корня проекта; --parent уже абсолютный
    path = layout.source_root((Path(repo) / parent).resolve())
    if layout.detect(path) is None:
        raise InfraError(f"parent = {parent}: нет файла конфигурации ({path}) — выгрузка основной конфигурации "
                         "не найдена; создайте каталог или ссылку на выгрузку типовой "
                         "(в рабочем дереве git worktree её нет), для push в обход — git push --no-verify",
                         code="no-parent")
    return path


def load(repo: str | Path, environ: Mapping[str, str] | None = None) -> Config:
    """Настройки проекта: DEFAULTS, otk.toml, otk.local.toml, поверх — переменные OTK_* (environ — для тестов)."""
    cfg, explicit = DEFAULTS, False
    for name in ("otk.toml", "otk.local.toml"):
        p = Path(repo) / name
        if p.exists():
            with open(p, "rb") as f:
                try:
                    data = tomllib.load(f)
                except tomllib.TOMLDecodeError as e:     # ValueError: код 2 одной строкой, config_invalid
                    raise ValueError(f"{p}: ошибка синтаксиса TOML: {e}") from e
            explicit = explicit or "source" in data.get("project", {})
            cfg = _merge(cfg, data)
    project = {**cfg["project"], "source": source_rel(repo, cfg["project"]["source"])}
    root = None if explicit else root_source(repo, project["source"])
    if root:
        project.update(source=root, sourceDetected=True)
    return {**cfg, "project": project, "environment": {**cfg["environment"], **env_values(environ)}}


def root_source(repo: str | Path, source: str) -> str | None:
    """project.source не задан, по умолчанию выгрузки нет, а в корне репозитория она есть — каталог выгрузки от
    корня ("." или src проекта EDT); иначе None."""
    if layout.detect(Path(repo) / source):
        return None
    root = layout.source_root(Path(repo))
    if layout.detect(root) is None:
        return None
    return root.relative_to(Path(repo)).as_posix()


def source_rel(repo: str | Path, source: str) -> str:
    """project.source / --source (от корня репозитория): каталог проекта EDT -> его src."""
    p = Path(repo) / source
    return source.rstrip("/") + "/src" if layout.source_root(p) != p else source


# аргументы окружения командной строки -> environment.<ключ>; пути — от текущего каталога
ENV_ARGS = {"ib": "ib", "connection": "connection", "user": "user",
            "platform": "platform", "yaxunit": "yaxunit", "bslls": "bslls", "edtcli": "edtcli", "run_dir": "runDir",
            "min_platform": "minPlatform", "bsl_analyzer": "bslAnalyzer", "analyzer": "analyzer"}
PATH_KEYS = {"ib", "platform", "yaxunit", "bslls", "bslAnalyzer", "edtcli", "runDir"}
# исполняемые файлы, которые можно задать и именем в PATH
PATH_NAMES = {"bslls", "bslAnalyzer", "edtcli"}


def _set(cfg: Config, assignment: str) -> None:
    """--set раздел.ключ=значение: значение — литерал TOML (true, 600, ["a"], "строка") или просто строка."""
    key, sep, raw = assignment.partition("=")
    parts = [p.strip() for p in key.split(".")]
    if not sep or len(parts) < 2 or not all(parts):
        raise ValueError(f"--set {assignment}: нужно раздел.ключ=значение")
    try:
        value = tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ValueError(f"--set {assignment}: {p} — не раздел")
    node[parts[-1]] = value


def apply_args(cfg: Config, a: Namespace) -> Config:
    """Аргументы командной строки поверх настроек (приоритет выше OTK_* и otk.local.toml): --set, затем ключи
    окружения (--ib, --yaxunit, …), --parent и --extension. Пароля в аргументах нет: его видно в списке
    процессов — только настройки или OTK_PASSWORD (load). Возвращает новый словарь."""
    cfg = copy.deepcopy(cfg)
    for assignment in getattr(a, "set", None) or []:
        _set(cfg, assignment)
    env, project = cfg["environment"], cfg["project"]
    for attr, key in ENV_ARGS.items():
        value = getattr(a, attr, None)
        if value:
            # bslls, bslAnalyzer, edtcli — и имя в PATH
            resolve = key in PATH_KEYS and (key not in PATH_NAMES or Path(value).exists())
            env[key] = str(Path(value).resolve()) if resolve else value
    if getattr(a, "no_compat_warnings", False):
        env["compatWarnings"] = False
    if getattr(a, "parent", None):
        project["parent"] = str(Path(a.parent).resolve())
    if getattr(a, "extension", None):
        project["extension"] = a.extension
    return cfg


def extension_name(source_root: str | Path) -> str | None:
    """Имя расширения по выгрузке source_root (в файле конфигурации есть признак расширения) или None —
    основная конфигурация либо это не выгрузка."""
    lay = layout.detect(source_root)
    props = lay.read_properties(source_root) if lay else None
    return props.name if props and props.extension else None


def detect_extension(cfg: Config, source_root: str | Path | None) -> str | None:
    """project.extension не задан — распознать расширение по выгрузке source_root (каталог с
    файлом конфигурации). Возвращает имя расширения или None."""
    project = cfg["project"]
    if not project.get("extension") and source_root:
        name = extension_name(source_root)
        if name:
            project["extension"] = name
            project["extensionDetected"] = True
    return project.get("extension")


NO_PARENT = ("расширение без выгрузки основной конфигурации (--parent): ссылки на её объекты и методы не "
             "проверить — такие находки статики понижены до предупреждений")
