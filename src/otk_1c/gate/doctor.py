"""Диагностика окружения (`otk doctor`): что найдено на машине и какие проверки реестра доступны."""
import shutil
import sys
from pathlib import Path

from .. import analyzers, config
from ..checks import CHECKS, DEP_CODES, DEPS, missing_keys, modes
from ..probes import desktop, platform
from ..probes.executors import com
from ..sources import contexts
from . import platform_api, reasons


def _ibcmd(bin_dir, env):
    """ibcmd есть — (True, путь); нет — что идёт медленнее или в обход (Platform.full_load, extension_unsafe)."""
    if platform.ibcmd_installed(bin_dir):
        return True, str(Path(bin_dir) / platform.exe_name("ibcmd"))
    tool = str(env.get("ibTool") or "designer").strip().lower()
    if tool == "ibcmd":
        return False, ("не найден, а environment.ibTool = ibcmd — сборка завершится сбоем: установите компоненты "
                       "сервера 1С:Предприятия или выберите ibTool = designer")
    return False, ("не найден (ставится с компонентами сервера 1С): полная загрузка — Конфигуратором, втрое "
                   "медленнее; безопасный режим расширений YAxUnit снимается через COM (без COM пробы YAxUnit "
                   "недоступны)")


def environment(cfg):
    """Зависимости на этой машине: {имя: (есть ли, пояснение)} — для doctor."""
    env = cfg["environment"]
    out = {"python": (sys.version_info >= (3, 11), sys.version.split()[0]),
           "git": (bool(shutil.which("git")), shutil.which("git")
                   or "не найден в PATH — проверка только каталогов выгрузки (--dir, --base-dir)")}
    configured = env.get("platform")
    bin_dir = Path(configured) if configured else platform.find_platform()
    version = platform.platform_version(bin_dir) if bin_dir and bin_dir.is_dir() else None
    out["platform"] = (bool(version),
                       f"{version} ({bin_dir})" if version else "не найдена (--platform, environment.platform)")
    if version:
        out["ibcmd"] = _ibcmd(bin_dir, env)
    target = env.get("connection") or env.get("ib")
    ib_dir = Path(env["ib"]) if env.get("ib") and not env.get("connection") else None
    if ib_dir is not None and (not ib_dir.exists() or ib_dir.is_dir() and not any(ib_dir.iterdir())):
        out["ib"] = (True, f"{target} — нет, будет создана при первой сборке")   # build.ensure_ib
    else:
        ib_ok = bool(env.get("connection")) or bool(ib_dir and ib_dir.is_dir())
        out["ib"] = (ib_ok, target or "не задана (--ib или --connection; без них — только статика)")
    cfe = env.get("yaxunit")
    out["yaxunit"] = (bool(cfe and Path(cfe).exists()), cfe or "не задан (--yaxunit; ОТК его не скачивает)")
    if not platform.WINDOWS:
        out["display"] = desktop.display_status()
        out["com"] = (False, "только Windows")
    elif env.get("executors", {}).get("server", "auto") == "yaxunit":
        out["com"] = (False, "отключён настройкой (environment.executors.server)")
    elif "ExternalConnection" not in env.get("contexts", ("ExternalConnection",)):
        out["com"] = (False, "не используется: внешнее соединение исключено настройкой (environment.contexts)")
    else:
        progid = env.get("comConnector") or (platform.com_progid(bin_dir) if bin_dir else "V83.COMConnector")
        ok, note = com.available(progid)
        out["com"] = (ok, progid if ok else note)
    picks = {fmt: analyzers.selected(env, fmt) for fmt in analyzers.FORMATS}
    used_any = {n for names, _ in picks.values() for n in names}
    found = {}
    for name in analyzers.NAMES:
        exe = analyzers.find(name, env)
        found[name] = bool(exe)
        used = "" if name in used_any else " (не выбран: environment.analyzer)"
        out[name] = (bool(exe), (str(exe) if exe else f"не найден ({analyzers.WHERE[name]})") + used)
    parts, ok, warnings = [], False, []
    for fmt, (names, warning) in picks.items():
        usable = [n for n in names if fmt in analyzers.SUPPORTS[n]]
        ok = ok or any(found[n] for n in usable)
        parts.append(f"{fmt}: {', '.join(analyzers.TITLES[n] for n in usable) or 'нет поддерживающего формат'}")
        if warning and warning not in warnings:
            warnings.append(warning)
    out["analyzer"] = (ok, "environment.analyzer — " + "; ".join(parts + warnings))
    exe = platform.find_edtcli(env.get("edtcli"))
    out["edtcli"] = (bool(exe), str(exe) if exe else "не найден (нужен только для исходников EDT): " + DEPS["edtcli"])
    try:
        help_ix, _, notes = platform_api.open_help(version, bin_dir, env)
        out["help"] = (True, "; ".join([f"справка {help_ix.version}", *notes]))
    except Exception as e:  # noqa: BLE001 — нет справки платформы: проверка недоступна, doctor продолжает
        out["help"] = (False, f"справка платформы недоступна: {e}")
    if version:
        files = contexts.table_files()
        best = contexts.nearest_table(version, files)
        exact = bool(best) and contexts.version_key(best) == contexts.version_key(version)
        out["availability"] = (exact, f"таблица доступности {best} ({files[best]})" if exact else
                               f"точной таблицы для {version} нет, берётся {best or '—'}; построить: "
                               "otk availability build")
    return out


def result(cfg):
    """Итог doctor данными (без проекта — его добавляет cli): {code, environment, checks, warnings, staticOnly};
    code 0 — доступны все проверки с режимом on, иначе 2 (окружение). Нет зависимости или недоступна проверка —
    с reasonCode и remediation (reasons.CODES)."""
    found = environment(cfg)
    mode, warnings = modes(cfg)
    warnings = config.warnings(cfg) + warnings
    have = {k: ok for k, (ok, _) in found.items()}
    # копия ИБ без платформы бесполезна; на Linux платформе нужен ещё дисплей (Xvfb)
    have["ib"] = have["ib"] and have["platform"] and have.get("display", True)
    env = []
    for name, (ok, detail) in found.items():
        row = {"name": name, "ok": ok, "detail": detail}
        if not ok:
            code_ = DEP_CODES.get(name, "other")
            row.update(reasonCode=code_, remediation=reasons.remediation(code_))
        env.append(row)
    code = 0 if have["python"] else 2          # git необязателен: без него — gate --dir
    rows = []
    for c in CHECKS:
        lacking = missing_keys(c.name, have)
        row = {"name": c.name, "title": c.title, "mode": mode[c.name],
               "state": "off" if mode[c.name] == "off" else "unavailable" if lacking else "available"}
        if row["state"] == "unavailable":
            codes = list(dict.fromkeys(DEP_CODES[alts[0]] for alts in lacking))
            row.update(missing=[" или ".join(DEPS[a] for a in alts) for alts in lacking], loses=c.loses,
                       reasonCodes=codes, remediation=[reasons.remediation(x) for x in codes],
                       fatal=mode[c.name] == "on")
            code = 2 if row["fatal"] else code
        rows.append(row)
    # значения из переменных OTK_* (сильнее файлов настроек): ошибка в переменной видна сразу
    given = config.env_values()
    from_env = {f"environment.{key}": var for var, key in config.ENV_VARS.items() if key in given}
    return {"code": code, "environment": env, "checks": rows, "warnings": warnings, "staticOnly": not have["platform"],
            "fromEnv": from_env}


def doctor(cfg):
    """(строки отчёта, код выхода): 0 — доступны все проверки с режимом on, иначе 2."""
    res = result(cfg)
    lines = [f"Из переменных (сильнее otk.toml): {', '.join(f'{k} = {v}' for k, v in res['fromEnv'].items())}", ""
             ] if res["fromEnv"] else []
    lines += ["Окружение:"]
    lines += [f"  {'+' if e['ok'] else '-'} {e['name']}: {e['detail']}" for e in res["environment"]]
    lines += ["", "Проверки:"]
    for c in res["checks"]:
        if c["state"] == "off":
            state = "выключена настройкой"
        elif c["state"] == "available":
            state = "доступна"
        else:
            state = f"НЕ доступна — не найдено: {', '.join(c['missing'])}; теряется: {c['loses']}"
            if c["fatal"]:
                state += " (режим on: прогон завершится сбоем инфраструктуры)"
        lines.append(f"  {c['name']} [{c['mode']}] {c['title']}: {state}")
    if res["staticOnly"]:
        lines += ["", "Без платформы 1С доступна только статика: otk gate --static-only"]
    lines += [f"предупреждение: {w}" for w in res["warnings"]]
    return lines, res["code"]
