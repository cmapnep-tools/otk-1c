"""Шаг run: сборка копии ИБ, затем исполнители реестра executors.EXECUTORS (design.md §14).

run.json (schema/run.schema.json): {"timings", "notes", "results", "notChecked", "infra"} — результаты проб и
решения реестра проверок, принятые в шаге. Модули — план и зависимые модули из static.json.
"""
import json
import time
from pathlib import Path

from .. import checks, model
from ..errors import InfraError
from ..lang import platform_texts
from ..probes import build, executors, ib_tools, userbase
from ..probes.executors import owner_of
from ..probes.executors.extended_check import CONTEXTS as EXT_CONTEXTS
from ..probes.platform import Platform, com_progid
from ..sources import gitdiff, layout


def ensure_tools(platform, run_dir, env, config_root, extra_renames=()):
    state = build.load_state(run_dir, platform.ib) or {}
    cfe = env.get("yaxunit")
    if not cfe or not Path(cfe).exists():
        raise InfraError("не задан YAxUnit: environment.yaxunit — путь к YAxUnit-*.cfe", code="no-yaxunit")
    renames = sorted(set(state.get("tools", {}).get("renames", [])) | set(extra_renames))
    version = ib_tools.tools_version(cfe, ib_tools.base_properties(config_root, getattr(platform, "bin", None)))
    current = state.get("tools", {})
    if current.get("version") == version and set(current.get("renames", [])) >= set(renames) and not extra_renames:
        return "актуальны"
    info = ib_tools.install(platform, run_dir, cfe, config_root, renames)
    state = build.load_state(run_dir, platform.ib) or {}
    state["tools"] = info
    build.save_state(run_dir, state, platform.ib)
    return f"установлены (переименования: {', '.join(renames) or 'нет'})"


def install_tools(session, platform, run_dir, env, config_root, extra_renames=()):
    """Инструменты: в своей копии — по состоянию (ensure_tools), в рабочей базе — на время проверки."""
    if session is None:
        return ensure_tools(platform, run_dir, env, config_root, extra_renames)
    cfe = env.get("yaxunit")
    if not cfe or not Path(cfe).exists():
        raise InfraError("не задан YAxUnit: environment.yaxunit — путь к YAxUnit-*.cfe", code="no-yaxunit")
    return session.install_tools(cfe, config_root, extra_renames)


def _normalize(results, modules):
    """Падения при старте — к модулю плана по имени из ошибки; одинаковые находки — один раз."""
    by_owner = {m["owner"]: m for m in modules}
    for alias, owner in platform_texts()["modules"].items():       # МодульСеанса -> SessionModule
        if owner in layout.ROOT_KINDS and owner in by_owner:
            by_owner[alias] = by_owner[owner]
    seen, out = set(), []
    for r in results:
        if not r.get("module") and r.get("owner"):
            entry = by_owner.get(owner_of(r["owner"]))
            if entry:
                r = {**r, "module": entry["path"], "owner": entry["owner"]}
        key = (r["executor"], r["kind"], r.get("module"), r.get("line"), r["outcome"], r["message"])
        if key in seen and r["outcome"] != "ok":
            continue
        seen.add(key)
        out.append(r)
    return out


def run(repo, plan, cfg, run_dir, tree_root, log=print, gate=None, static=None):
    """Шаг run. environment.connection — рабочая база пользователя (userbase.py): всё, что ОТК в неё
    поставил, после проверки возвращается к прежнему виду — и при сбое тоже. gate (checks.Gate) — какие
    проверки выполнять; без него — по [checks] настроек. static — результат шага static (зависимые модули)."""
    gate = gate or checks.Gate(cfg)
    env = cfg["environment"]
    own, plan = plan, model.with_dependents(plan, static)       # run.json — с отпечатком плана collect
    if not env.get("connection"):
        out = _run(repo, plan, cfg, run_dir, tree_root, log, gate=gate)
        return write(run_dir, {**out, "notes": base_notes(plan) + out["notes"]}, gate, own)
    session = userbase.Session(Platform(env, Path(run_dir)), run_dir, bool(env.get("keepTools", False)), log)
    session.platform.unlock()             # база занята (Конфигуратор, сеанс) — не начинать, ничего не трогать
    try:
        out = _run(repo, plan, cfg, run_dir, tree_root, log, session, gate)
    except BaseException as e:
        restore(session, log, e)
        raise
    restored = restore(session, log)
    out["notes"].append(restored)
    if session.unrestored:                # проверка прошла, а база не возвращена — не зелёный прогон
        out["infra"] = out.get("infra", []) + [{"message": restored, "reasonCode": "restore-failed"}]
    return write(run_dir, {**out, "notes": base_notes(plan) + out["notes"]}, gate, own)


def restore(session, log, error=None):
    """Вернуть рабочую базу (userbase.Session.restore), текст итога. error — сбой проверки: база не
    возвращена — InfraError restore-failed с обеими причинами вместо исходной."""
    restored = session.restore()
    log(restored)
    if error is not None and session.unrestored:
        raise InfraError(f"{restored}; до этого проверка прервалась: {error}", code="restore-failed") from error
    return restored


def base_notes(plan):
    """Примечание шага run без git с «было» (контракт загрузки — build.build_dirs)."""
    return (["копия ИБ считается загруженной выгрузкой «было» (--base-dir): загружены только отличия"]
            if plan.get("baseDir") and not plan.get("vendor") else [])


def write(run_dir, out, gate, plan):
    """run.json: результат шага, решения реестра проверок с прошлого файла шага и отпечаток плана (plan —
    план collect, без зависимых). Пробы не выполнялись (нет копии ИБ, --static-only, сбой) — out без
    результатов: решения и примечания всё равно в run.json."""
    out = {**model.header("run", internal=True), "timings": {}, "notes": [], "results": [], **out}
    taken = gate.take()
    out["notChecked"] = out.get("notChecked", []) + taken["notChecked"]
    out["infra"] = out.get("infra", []) + taken["infra"]
    out["plan"] = model.plan_id(plan)
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "run.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def _build(repo, plan, cfg, run_dir, tree_root, platform, session):
    """Копия ИБ с проверяемой редакцией: рабочая база, выгрузка без git или редакция git."""
    env, src = cfg["environment"], plan["source"]
    extension = cfg["project"].get("extension")
    if plan.get("vendor"):                # gate --vendor: проверяемая редакция — сама конфигурация ИБ
        platform.unlock()
        return {"action": "проверяемая редакция — конфигурация ИБ (--vendor): загрузка не нужна"}
    if session and extension:
        return session.load_project_extension(Path(tree_root) / src, extension)
    if session and not env.get("loadProject"):
        platform.unlock()                 # база занята — проверка не начинается
        return {"action": "рабочая база: основная конфигурация считается загруженной (loadProject — загружать)"}
    if plan.get("headDir"):               # без git: контракт — в копии уже «было» (build.build_dirs)
        return build.build_dirs(plan, platform, run_dir, extension=extension)
    return build.build(repo, plan["headSha"] or gitdiff.rev(repo, "HEAD"), src, tree_root, platform, run_dir,
                       extension=extension)


RUNTIME = ("probes-server", "probes-client", "queries", "scenarios", "smoke")   # исполняют конфигурацию БД


def db_config_changed(platform, progid):
    """КонфигурацияИзменена() через COM: основная конфигурация ИБ не применена к БД, и пробы исполнения проверили
    бы прежний код из БД. None — проверить не удалось (нет COM, вход, сбой подключения)."""
    try:
        import win32com.client
        auth = f'Usr="{platform.user}";Pwd="{platform.password}";' if platform.user else ""
        conn = win32com.client.Dispatch(progid).Connect(platform.com_connection() + auth)
        changed = bool(conn.КонфигурацияИзменена())
        del conn
        return changed
    except Exception:  # noqa: BLE001 - pywin32, коннектор, вход: судить нельзя
        return None


def _apply_db_config(ctx, session):
    """Без загрузки ОТК (--vendor, рабочая база без loadProject) основную конфигурацию мог изменить разработчик, не
    обновив БД. Копия ИБ — /UpdateDBCfg (и когда проверить не удалось); рабочая база не меняется — пробы
    исполнения «не проверено». Возвращает примечание или None."""
    platform = ctx.platform
    platform.unlock()
    changed = db_config_changed(platform, ctx.env.get("comConnector") or com_progid(platform.bin))
    if changed is False:
        return None
    if session:
        if changed:
            for name in RUNTIME:
                ctx.gate.block(name, "основная конфигурация рабочей базы не применена к БД (КонфигурацияИзменена): "
                                     "пробы исполнили бы прежний код — обновите конфигурацию базы данных")
            return "конфигурация БД рабочей базы не обновлена: пробы исполнения не выполнялись"
        return None
    rc, sec, out = platform.update_db()
    if rc:
        raise InfraError(f"обновление конфигурации БД: rc={rc} {out[-500:]}", code="platform-failed")
    ctx.timings["update"] = sec
    why = "основная конфигурация не была применена к БД" if changed else "проверить применение не удалось (COM)"
    return f"конфигурация БД копии обновлена ({round(sec, 1)} c): {why}"


def _run(repo, plan, cfg, run_dir, tree_root, log=print, session=None, gate=None):
    """Сборка копии ИБ, затем исполнители реестра executors.EXECUTORS по порядку: сначала каждый выбирает
    своё (решения реестра проверок — в порядке реестра), затем выполняет выбранное и переданное ему."""
    env = cfg["environment"]
    run_dir = Path(run_dir)
    platform = session.platform if session else Platform(env, run_dir)
    parent = cfg["project"].get("parent")
    config_root = (Path(repo) / parent).resolve() if parent else Path(tree_root) / plan["source"]
    # выгрузка новее платформы не загрузится («Неизвестная версия формата»): сказать это до сборки копии ИБ
    ib_tools.require_format(config_root, getattr(platform, "bin", None))

    t = time.time()
    try:
        b = _build(repo, plan, cfg, run_dir, tree_root, platform, session)
    except build.LoadError as e:
        # выгрузка не загружается из-за ошибок в файлах: это находки; пробы без загруженной редакции невозможны
        results = [{"executor": "build", "kind": "load", "module": rel, "owner": rel, "context": "Designer",
                    "outcome": "compile-error", "message": message, "line": None} for rel, message in e.errors]
        log(f"сборка: ошибки загрузки выгрузки ({len(results)})")
        return {"timings": {"build": round(time.time() - t, 1)},
                "notes": ["пробы не выполнялись: выгрузка не загружается в копию ИБ"], "results": results}
    ctx = executors.RunContext(
        repo, plan, cfg, run_dir, tree_root, config_root, platform, gate or checks.Gate(cfg),
        install=lambda renames: install_tools(session, platform, run_dir, env, config_root, renames),
        session=session, log=log, designer_dump=None if parent else b.get("dump"))
    ctx.timings["build"] = round(time.time() - t, 1)
    if "ibDump" in b:                     # модули EDT без экспорта
        ctx.timings["ibDump"] = b["ibDump"]
        edt_note = f", без экспорта EDT: выгрузка ИБ {b['ibDump']} c)"
    else:
        edt_note = f", экспорт EDT {b['export']} c)" if b.get("export") else ")"
    for key in ("edtNote", "loadNote"):
        if b.get(key):
            ctx.notes.append(b[key])
            log(f"сборка: {b[key]}")
    log(f"сборка: {b['action']} ({ctx.timings['build']} c" + edt_note)
    if plan.get("vendor") or (session and not cfg["project"].get("extension") and not env.get("loadProject")):
        note = _apply_db_config(ctx, session)
        if note:
            ctx.notes.append(note)
            log(f"сборка: {note}")
    cfe = env.get("yaxunit")
    ctx.have.update({"ib": True, "yaxunit": bool(cfe and Path(cfe).exists())})

    chosen = [(ex, ex.select(ctx)) for ex in executors.EXECUTORS]
    for ex, items in chosen:
        if not (items or ctx.handed.get(ex.name)):
            continue
        t = time.time()
        res, note = ex.run(ctx, items)
        ctx.results += res
        if note:
            ctx.notes.append(note)
        ctx.timings[ex.name] = round(ctx.timings.get(ex.name, 0) + time.time() - t, 1)

    for m in plan["modules"]:
        for c in m.get("uncovered", []):
            if m["path"] in ctx.covered and (c["context"] in EXT_CONTEXTS or c["context"] == "*"
                                             and "OrdinaryClient" not in m.get("contexts", [])):
                continue              # проверено extcheck во всех режимах
            ctx.results.append({"executor": "plan", "kind": "uncovered", "module": m["path"], "owner": m["owner"],
                                "context": c["context"], "needs": c["needs"], "outcome": "skipped",
                                "message": c["reason"], "line": None})
    return {"timings": ctx.timings, "notes": ctx.notes, "results": _normalize(ctx.results, plan["modules"])}
