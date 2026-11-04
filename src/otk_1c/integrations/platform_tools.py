"""Хуки VS Code «1C: Platform Tools»: проверка расширения в той базе, куда оно загружается.

    otk pt pre    до загрузки: сохранить текущее расширение целевой базы в .cfe
    otk pt post   после загрузки: статика + проверка расширения Конфигуратором в целевой базе;
                       ошибки — откат к сохранённому состоянию (--no-static — без статики: загружен .cfe)
    otk pt check  до обновления БД или выгрузки в .cfe: проверка расширения в базе; ошибки — команда отменяется
    otk pt static до сборки .cfe: статика исходников; ошибки — сборка отменяется
    otk pt cf-pre  до загрузки основной конфигурации или обновления БД: статика изменённых модулей
                        рабочего дерева; ошибки — команда не выполняется
    otk pt cf-post после загрузки основной конфигурации: расширенная проверка изменённых общих модулей
                        (временное расширение, без применения к БД) и, если БД обновлена, пробы COM; без отката

Какая команда Platform Tools выполняется, хук узнаёт из VRUNNER_COMMAND_ID (заголовок сводки).
При ошибках сводка открывается в VS Code.

Копию большой базы снимать дорого, а копии нужной базы может и не быть, поэтому загрузка идёт штатно,
а проверка — после неё, в той же базе. Целевую базу и вход Platform Tools передаёт хуку в VRUNNER_ARGS
(`--settings <профиль>.json` с `ibconnection`, `db-user`, `db-pwd`, `v8version`, или сами ключи).
Platform Tools на ненулевой код `post` не реагирует (только пишет в журнал), поэтому откат делает сам хук.
Образец .1cpt/hooks.json — docs/platform-tools/hooks.json.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .. import config, model
from ..errors import InfraError
from ..gate import pipeline, report
from ..probes import designer_check
from ..probes.executors import com, extended_check
from ..probes.platform import Platform, com_progid, find_platform, install_roots
from ..sources import gitdiff, layout, workspace
from . import history

KEYS = ("ibconnection", "db-user", "db-pwd", "v8version")


def parse_args(args_line, cwd):
    """Соединение из строки аргументов vrunner: ключи --ibconnection/--db-user/... и файл --settings.
    Возвращает {ibconnection, db-user, db-pwd, v8version, updatedb, extension}."""
    try:
        tokens = shlex.split(args_line or "", posix=False)
    except ValueError:
        tokens = (args_line or "").split()
    tokens = [t.strip('"') for t in tokens]
    # vanessa-runner 2: compileext … --updatedb; vanessa-runner 3: cfe load … (обновление БД по умолчанию,
    # «Только загрузить» — --no-update-db)
    v3_load = tokens[:2] in (["cfe", "load"], ["cf", "load"])
    out = {"updatedb": "--updatedb" in tokens or tokens[:2] == ["infobase", "update"]
           or (v3_load and "--no-update-db" not in tokens)}
    settings = None
    for i, tok in enumerate(tokens):
        key, _, inline = tok.partition("=")
        value = inline or (tokens[i + 1] if i + 1 < len(tokens) else None)
        if key == "--settings":
            settings = value
        elif key.lstrip("-") in KEYS and key.startswith("--"):
            out[key.lstrip("-")] = value
    if "--extension-name" in tokens:
        i = tokens.index("--extension-name")
        out["extension"] = tokens[i + 1] if i + 1 < len(tokens) else None
    elif len(tokens) >= 3 and tokens[0] == "compileext":
        out["extension"] = tokens[2]
    if settings:
        path = Path(settings) if Path(settings).is_absolute() else Path(cwd) / settings
        for k, v in read_profile(path).items():
            out.setdefault(k, v)
    return out


def read_profile(path):
    """Профиль vrunner: {"vrunner": {"ibconnection": ...}} (Platform Tools) или {"default": {"--ibconnection": ...}}."""
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    section = data.get("vrunner") or data.get("default") or {}
    return {k.lstrip("-"): v for k, v in section.items() if k.lstrip("-") in KEYS}


def platform_for(conn, run_dir):
    env = {"connection": conn["ibconnection"], "user": conn.get("db-user", ""), "password": conn.get("db-pwd", "")}
    version = conn.get("v8version")
    if version:
        for root in install_roots():
            if (root / version / "bin").is_dir():
                env["platform"] = str(root / version / "bin")
                break
    if "platform" not in env and not find_platform():
        raise InfraError("платформа 1С не найдена", code="no-platform")
    return Platform(env, run_dir)


def state_dir(cfg, repo, conn):
    key = hashlib.sha1(conn["ibconnection"].encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return workspace.run_dir(cfg, repo) / "pt" / key


def pre(repo, cfg, conn, log=print):
    ext = conn.get("extension") or cfg["project"].get("extension")
    if not conn.get("ibconnection") or not ext:
        log("otk pt pre: нет базы (--ibconnection/--settings) или имени расширения — проверка пропущена")
        return 0
    d = state_dir(cfg, repo, conn)
    d.mkdir(parents=True, exist_ok=True)
    backup = d / f"{ext}.cfe"
    backup.unlink(missing_ok=True)
    # прежнее состояние — долой: если сохранение не удастся, onError не должен «откатить» к старой копии
    (d / "state.json").unlink(missing_ok=True)
    platform = platform_for(conn, d)
    rc, sec, out = platform.save_extension(ext, backup, label="pt-backup")
    if rc == 0 and backup.exists():
        existed = True
    elif "не найден" in out.lower() or "not found" in out.lower():
        existed = False
    else:
        log(f"otk pt pre: не удалось сохранить расширение {ext} ({out[-300:]}) — загрузка отменена, "
            f"иначе откатить её будет не к чему")
        return 2
    (d / "state.json").write_text(json.dumps({"connection": conn["ibconnection"], "extension": ext, "existed": existed,
                                              "backup": str(backup), "time": time.time(),
                                              "updatedb": conn.get("updatedb", False)}, ensure_ascii=False),
                                  encoding="utf-8")
    log(f"otk pt pre: расширение {ext} {'сохранено' if existed else 'в базе отсутствует'} ({sec} c)")
    return 0


TITLES = {
    "1c-platform-tools.cfe.load": "Загрузка расширения {ext} отменена",
    "1c-platform-tools.cfe.loadByList": "Загрузка расширения {ext} отменена",
    "1c-platform-tools.cfe.loadFile": "Загрузка расширения {ext} из *.cfe отменена",
    "1c-platform-tools.cfe.updateDb": "Обновление расширения {ext} в ИБ отменено",
    "1c-platform-tools.cfe.unload": "Выгрузка расширения {ext} в *.cfe отменена",
    "1c-platform-tools.cfe.compile": "Сборка *.cfe расширения {ext} отменена",
    "1c-platform-tools.cf.load": "Загрузка конфигурации отменена",
    "1c-platform-tools.cf.loadIncrement": "Загрузка изменений конфигурации отменена",
    "1c-platform-tools.cf.loadByList": "Загрузка конфигурации по списку отменена",
    "1c-platform-tools.infobase.updateDb": "Обновление конфигурации в ИБ отменено",
}
DEFAULT_TITLE = {"post": "Загрузка расширения {ext} отменена", "check": "Команда с расширением {ext} отменена",
                 "static": "Сборка *.cfe расширения {ext} отменена", "cf-pre": "Команда с конфигурацией отменена"}


def title(action, ext, command_id=None):
    return (TITLES.get(command_id or "") or DEFAULT_TITLE[action]).format(ext=ext) + ": найдены ошибки"


def run_checks(repo, cfg, conn, d, static=True, designer=True, log=print, com=False):
    """Статика по рабочему дереву и/или проверка расширения Конфигуратором в целевой базе; com — пробы
    изменённых модулей и разбор запросов через внешнее соединение (только когда расширение применено к БД).
    Возвращает (ошибки, примечания)."""

    findings, notes = [], []
    ext = conn.get("extension") or cfg["project"].get("extension")
    src = cfg["project"]["source"]
    if static:
        base = cfg.get("hook", {}).get("base", "origin/master")
        try:
            s = pipeline.gate_run(repo, cfg, d / "static", pipeline.GateOptions(base, worktree=True, static_only=True),
                                  log=lambda m: None)
            findings += [f for f in s["findings"] if f["severity"] == "error"]
        except (gitdiff.GitError, InfraError) as e:
            notes.append(f"статика не выполнена: {e}")
    if designer:
        platform = platform_for(conn, d)
        modes = designer_check.default_modes(_config_root(repo, cfg),
                                             cfg["environment"].get("ordinaryApplication", False))
        rc, sec, out = platform.designer("/CheckModules", *modes, "-Extension", ext, label="pt-check")
        found = designer_check.parse(out, src, layout.detect(Path(repo) / src) or layout.DESIGNER)
        findings += [{"severity": "error", "step": "designer", "rule": "CheckModules", "file": f["file"],
                      "line": f["line"], "column": f["col"], "message": f["message"]} for f in found]
        if rc and not found:
            notes.append(f"Конфигуратор: rc={rc} без разобранных ошибок: {' '.join(out.split())[-300:]}")
            findings.append({"severity": "error", "step": "designer", "rule": "CheckModules", "file": None,
                             "line": None, "column": None, "message": f"проверка завершилась с кодом {rc}"})
        log(f"otk pt: Конфигуратор {sec} c")
    if com:
        found, note = com_checks(cfg, conn, d, log)
        findings += found
        if note:
            notes.append(note)
    return findings, notes


def com_checks(cfg, conn, d, log=print):
    """Пробы COM и разбор запросов по плану статики (изменённые модули рабочего дерева) в целевой базе.
    Только чтение: в базу ничего не ставится. Возвращает (ошибки, примечание)."""

    plan_file = d / "static" / "otk.json"
    if not plan_file.exists():
        return [], "пробы COM не выполнены: нет плана статики"
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    plan = model.with_dependents(plan, report.step_for(plan, plan_file.with_name("static.json"))[0])
    platform = platform_for(conn, d)
    progid = cfg["environment"].get("comConnector") or com_progid(platform.bin)
    ok, why = com.available(progid)
    if not ok:
        return [], f"пробы COM не выполнены: {why}"
    modules = [m for m in plan["modules"] if com.suitable(m)]
    queries = [q for q in plan["queries"] if q.get("complete")]
    if not modules and not queries:
        return [], ""
    t = time.time()
    try:
        results, note = com.run(platform, plan, modules, queries, progid)
    except com.Unavailable as e:          # в рабочую базу хук YAxUnit не ставит — обхода нет, только примечание
        return [], f"пробы COM не выполнены: {e}"
    src = plan["source"].strip("/")
    downgrade = report.query_downgrades(plan)
    failed = [r for r in results if r["outcome"] in model.FAIL]
    found = [{"severity": "error", "step": f"com:{r['kind']}", "rule": r["outcome"],
              "file": f"{src}/{r['module']}" if r.get("module") else None, "line": r.get("line"),
              "column": None, "message": r["message"]}
             for r in failed if r.get("query") not in downgrade]
    # запрос не менялся или собран подстановкой — предупреждение: загрузку не отменяет
    notes = [f"COM: {note}"] if note else []
    notes += [f"предупреждение: {src}/{r['module']}:{r.get('line')} — {downgrade[r['query']]}: {r['message']}"
              for r in failed if r.get("query") in downgrade]
    log(f"otk pt: COM — модулей {len(modules)}, запросов {len(queries)}, ошибок {len(found)} "
        f"({time.time() - t:.1f} c)")
    return found, "; ".join(notes)


def post(repo, cfg, conn, log=print, open_summary=True, static=True, command_id=None, on_error=False):
    """После загрузки: проверка, при ошибках — откат. on_error — сама загрузка завершилась ошибкой (Platform
    Tools вызывает onError вместо post: например, при загрузке с обновлением БД не стартовал сеанс 1С с новым
    расширением): состояние расширения неизвестно — откат всегда, проверка — чтобы показать причину."""
    ext = conn.get("extension") or cfg["project"].get("extension")
    if not conn.get("ibconnection") or not ext:
        log("otk pt post: нет базы или имени расширения — проверка пропущена")
        return 0
    d = state_dir(cfg, repo, conn)
    state_file = d / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
    # пробы COM — только когда расширение применено к БД (загрузка с обновлением) и загрузка прошла
    findings, notes = run_checks(repo, cfg, conn, d, static=static, log=log,
                                 com=bool(conn.get("updatedb")) and static and not on_error)
    log(f"otk pt post: ошибок {len(findings)}")
    if not findings and not on_error:
        return 0
    rolled, ok = (rollback(platform_for(conn, d), state, ext, conn, log) if state
                  else ("отката нет: состояние до загрузки не сохранено", True))
    if on_error:
        heading = title("post", ext, command_id).replace(": найдены ошибки", ": команда завершилась ошибкой")
        notes.insert(0, "Platform Tools сообщил об ошибке загрузки"
                        + (" — расширение восстановлено к состоянию до неё" if state and ok else "")
                        + ("" if findings else "; проверка расширения ошибок не нашла — причина в журнале команды"))
    else:
        heading = title("post", ext, command_id)
    _report(d, heading, findings, notes, rolled, log, open_summary)
    return 1 if ok else 2                 # база не возвращена — сбой инфраструктуры (restore-failed), не дефект


def check(repo, cfg, conn, log=print, open_summary=True, command_id=None):
    """До обновления БД или выгрузки в .cfe: проверка расширения в базе; ненулевой код отменяет команду."""
    ext = conn.get("extension") or cfg["project"].get("extension")
    if not conn.get("ibconnection") or not ext:
        log("otk pt check: нет базы или имени расширения — проверка пропущена")
        return 0
    d = state_dir(cfg, repo, conn)
    d.mkdir(parents=True, exist_ok=True)
    findings, notes = run_checks(repo, cfg, conn, d, static=False, log=log)
    log(f"otk pt check: ошибок {len(findings)}")
    if not findings:
        return 0
    _report(d, title("check", ext, command_id), findings, notes, "команда не выполнялась", log, open_summary)
    return 1


def static_only(repo, cfg, conn, log=print, open_summary=True, command_id=None):
    """До сборки .cfe: статика исходников; ненулевой код отменяет сборку."""
    ext = conn.get("extension") or cfg["project"].get("extension") or "?"
    d = workspace.run_dir(cfg, repo) / "pt" / "static"
    d.mkdir(parents=True, exist_ok=True)
    findings, notes = run_checks(repo, cfg, conn, d, static=True, designer=False, log=log)
    log(f"otk pt static: ошибок {len(findings)}")
    if not findings:
        return 0
    _report(d, title("static", ext, command_id), findings, notes, "сборка не выполнялась", log, open_summary)
    return 1


def _config_project(cfg, action, log):
    """Команды основной конфигурации проверяют проект-конфигурацию; у проекта-расширения код в расширении."""
    if cfg["project"].get("extension"):
        log(f"otk pt {action}: проект — расширение {cfg['project']['extension']}: команда основной "
            "конфигурации к его коду не относится — проверка пропущена")
        return False
    return True


def cf_pre(repo, cfg, conn, log=print, open_summary=True, command_id=None):
    """До загрузки основной конфигурации или обновления БД: статика изменённых модулей рабочего дерева
    (относительно [hook] base); ненулевой код отменяет команду — не тратим время на загрузку и запуск."""
    if not _config_project(cfg, "cf-pre", log):
        return 0
    d = state_dir(cfg, repo, conn) if conn.get("ibconnection") else workspace.run_dir(cfg, repo) / "pt" / "static"
    d.mkdir(parents=True, exist_ok=True)
    findings, notes = run_checks(repo, cfg, conn, d, static=True, designer=False, log=log)
    log(f"otk pt cf-pre: ошибок {len(findings)}")
    if not findings:
        return 0
    _report(d, title("cf-pre", "", command_id), findings, notes, "команда не выполнялась", log, open_summary)
    return 1


def cf_post(repo, cfg, conn, log=print, open_summary=True, command_id=None, on_error=False):
    """После загрузки основной конфигурации: расширенная проверка изменённых общих модулей (временное
    расширение без применения к БД, удаляется) и, если БД обновлена, пробы COM по плану статики cf-pre.
    Без отката: сохранять и возвращать большую конфигурацию дорого — статика до загрузки дешевле."""
    if not _config_project(cfg, "cf-post", log) or not conn.get("ibconnection"):
        return 0
    if on_error:
        log("otk pt cf-post: команда завершилась ошибкой — проверка не выполняется")
        return 0
    d = state_dir(cfg, repo, conn)
    plan_file = d / "static" / "otk.json"
    if not plan_file.exists():
        log("otk pt cf-post: нет плана статики (хук cf-pre не выполнялся) — проверка пропущена")
        return 0
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    src = plan["source"].strip("/")
    common = [m for m in plan["modules"] if m["kind"] == "CommonModule" and m.get("reason") == "changed"]
    findings, notes = [], []
    if common and layout.get(plan.get("sourceFormat")) is not layout.DESIGNER:
        # описания для заимствования — из выгрузки Конфигуратора; у gate на EDT — частичная выгрузка своей копии ИБ
        #, а здесь база — рабочая база пользователя
        notes.append(f"расширенная проверка изменённых общих модулей ({len(common)}) не выполнялась: исходники EDT "
                     "в pt cf-post не поддерживаются — проверьте их otk gate в копии ИБ")
        log(f"otk pt cf-post: {notes[-1]}")
        common = []
    if common:
        platform = platform_for(conn, d)
        t = time.time()

        def read(ref, rel):
            return gitdiff.show(repo, ref, f"{src}/{rel}")

        results, note = extended_check.run(
            platform, d, Path(repo) / src, common,
            read_base=lambda e: (read(plan["baseSha"], e.get("renamedFrom") or e["path"])
                                 if e["status"] != "A" else None),
            read_head=lambda e: (Path(repo) / src / e["path"]).read_bytes().decode("utf-8-sig"),
            read_xml=lambda e: (Path(repo) / src / layout.get(plan.get("sourceFormat"))
                                .object_file("CommonModules", e["owner"].split(".", 1)[1]))
            .read_bytes().decode("utf-8-sig"))
        findings += [{"severity": "error", "step": "extcheck", "rule": r["outcome"],
                      "file": f"{src}/{r['module']}" if r.get("module") else None, "line": r.get("line"),
                      "column": None, "message": r["message"]}
                     for r in results if r["outcome"] in model.FAIL]
        if note:
            notes.append(note)
        log(f"otk pt cf-post: расширенная проверка общих модулей ({len(common)}) {time.time() - t:.1f} c")
    if conn.get("updatedb"):
        found, note = com_checks(cfg, conn, d, log)
        findings += found
        if note:
            notes.append(note)
    log(f"otk pt cf-post: ошибок {len(findings)}")
    if not findings:
        return 0
    heading = TITLES.get(command_id or "") or "Загрузка конфигурации"
    heading = heading.replace(" отменена", "").replace(" отменено", "")
    _report(d, f"{heading}: найдены ошибки", findings, notes,
            "конфигурация загружена — исправьте ошибки и загрузите снова (отката нет)", log, open_summary)
    return 1


def _report(d, heading, findings, notes, outcome, log, open_summary):
    seen, unique = set(), []
    for f in findings:                     # одна ошибка в нескольких режимах проверки — один раз
        key = (f.get("file"), f.get("line"), f.get("message"))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    findings = unique
    summary = d / "pt-check.md"
    summary.write_text(markdown(heading, findings, notes, outcome), encoding="utf-8")
    log(f"otk pt: {outcome}; сводка {summary}")
    if open_summary and shutil.which("code"):
        subprocess.run(["code", "-r", str(summary)], shell=os.name == "nt", stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def rollback(platform, state, ext, conn, log):
    """Откат к состоянию из pre: (текст итога, удался ли каждый шаг)."""
    if state["existed"]:
        rc, _, out = platform.load_extension(ext, state["backup"], label="pt-restore")
        action = "расширение возвращено к состоянию до загрузки"
    else:
        rc, _, out = platform.delete_extension(ext, label="pt-restore")
        action = "расширение удалено (до загрузки его в базе не было)"
    if rc:
        return (f"ОТКАТ НЕ УДАЛСЯ (rc={rc}): {' '.join(out.split())[-300:]} — "
                f"восстановите расширение вручную из {state['backup']}"), False
    if conn.get("updatedb") or state.get("updatedb"):
        rc, _, out = platform.update_db(extension=ext, label="pt-restore-updatedb")
        if rc:
            return (f"ОТКАТ НЕ ЗАВЕРШЁН: {action}, но обновление конфигурации БД не удалось (rc={rc}): "
                    f"{' '.join(out.split())[-300:]} — обновите конфигурацию БД вручную"), False
        action += "; конфигурация БД обновлена"
    return action, True


def markdown(heading, findings, notes, outcome):
    lines = [f"# {heading}", "", f"**{outcome}**", "", f"## Ошибки ({len(findings)})"]
    for f in findings:
        where = f"`{f['file']}:{f['line']}`" if f.get("file") else "—"
        lines.append(f"- {where} — {f['message']} _({f['step']}:{f['code']})_")
    if notes:
        lines += ["", "## Примечания"] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"


def _config_root(repo, cfg):
    parent = config.parent_dir(repo, cfg)
    return parent or Path(repo) / cfg["project"]["source"]


def main(a):
    repo = Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
    cfg = config.load(repo)
    conn = parse_args(a.args if a.args is not None else os.environ.get("VRUNNER_ARGS", ""),
                      os.environ.get("VRUNNER_CWD") or repo)
    log = lambda m: print(m, flush=True)   # noqa: E731
    command_id = os.environ.get("VRUNNER_COMMAND_ID")
    started, code = time.time(), 2
    try:
        code = _dispatch(a, repo, cfg, conn, log, command_id)
        return code
    finally:
        history.record(cfg, repo, "pt", a.action, code, started, command=command_id)


def _dispatch(a, repo, cfg, conn, log, command_id):
    try:
        if a.action == "pre":
            return pre(repo, cfg, conn, log)
        if a.action == "post":
            return post(repo, cfg, conn, log, static=not a.no_static, command_id=command_id, on_error=a.on_error)
        if a.action == "check":
            return check(repo, cfg, conn, log, command_id=command_id)
        if a.action == "cf-pre":
            return cf_pre(repo, cfg, conn, log, command_id=command_id)
        if a.action == "cf-post":
            return cf_post(repo, cfg, conn, log, command_id=command_id, on_error=a.on_error)
        return static_only(repo, cfg, conn, log, command_id=command_id)
    except InfraError as e:
        log(f"otk pt {a.action}: {e}")
        return 2
