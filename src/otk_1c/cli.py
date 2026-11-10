"""Командная строка ОТК.

    otk collect --base <ref> [--head <ref>|--worktree] [--repo .] [--source src/cf] [--out файл]
    otk gate --base <ref> [...]              вся проверка (см. otk gate -h)
    otk gate --base <ref> --designer         плюс все модули Конфигуратором в той же копии ИБ, одна сводка
    otk gate --dir <выгрузка> [--base-dir <было>]  без git: каталог целиком или отличия от «было»
    otk gate --dir <расширение> --parent <конфигурация> [--ib <копия ИБ>] [--yaxunit <.cfe>]
                                                 только командная строка, без otk.toml
    otk gate ... --json / otk doctor --json  итог одним объектом JSON в stdout (schema/*-result.schema.json)
    otk hook install|uninstall [--repo .]   git-хук pre-push (см. hook.py)
    otk designer-check [--repo .]           все модули Конфигуратором (см. designer_check.py)
    otk ci gate [--designer]|designer-check [--artifacts <каталог>]  запуск в CI (см. ci.py, docs/ci.md)

Коды выхода: 0 — успех, 1 — найдены ошибки, 2 — сбой (git, неподдерживаемый вариант языка и т.п.).
"""
import argparse
import contextlib
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import __version__, config, lang, model
from .errors import InfraError
from .extras import availability, console, controls, mcp, scaffold, techlog
from .gate import collect as collect_mod, doctor, output, pipeline, reasons, report, report_html, runner
from .integrations import ci as ci_mod, hook, platform_tools as pt_mod
from .probes import build, designer_check, syntax
from .probes.platform import Platform, find_platform, ib_path, platform_version
from .sources import contexts, gitdiff, layout, workspace

# ошибки, которые main печатает одной строкой с кодом 2 (gate --json — итогом со status infra)
USER_ERRORS = (gitdiff.GitError, lang.UnsupportedScriptVariant, InfraError, FileExistsError, ValueError,
               console.ConsoleError)
DESIGNER_STATIC = "--designer несовместим с --static-only (проверка Конфигуратором — в копии ИБ)"


def _project(a):
    """Каталог проекта (otk.toml, каталог прогона): корень git-репозитория --repo или, без git
    (gate --dir, git не установлен, каталог не репозиторий), сам каталог --repo."""
    if not getattr(a, "dir", None) and not getattr(a, "vendor", False):
        try:
            return Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
        except (gitdiff.GitError, OSError):
            if a.cmd not in ("static", "doctor", "build", "run", "report"):
                raise
    return Path(a.repo).resolve()


def _cfg(a, repo):
    """Настройки прогона: otk.toml + otk.local.toml + аргументы командной строки."""
    return config.apply_args(config.load(repo), a)

def cmd_console(a):
    if getattr(a, "json", False):
        return _json_out(a, _console_json, lambda text, code: reasons.command_error("console", text, code))
    try:
        res, code = _console_result(a)
    except console.ConsoleError as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return code


def _console_result(a):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = sys.stdin.read() if a.text == "-" else a.text
    if a.action != "bases" and not (a.ib and (text or a.action == "eventlog")):
        raise console.ConsoleError("нужны --ib и текст (запрос или имя объекта)")
    con = console.Console(idle=0)
    try:
        args = {"base": a.ib, "text": text, "name": text, "limit": a.limit,
                "params": json.loads(a.params) if a.params else None,
                "filter": json.loads(a.filter) if a.filter else None,
                "group_by": a.group.split(",") if a.group else None}
        res = mcp.call(con, {"query": "ib_query", "check": "ib_check", "metadata": "ib_metadata",
                             "bases": "ib_bases", "eventlog": "ib_eventlog"}[a.action], args)
    finally:
        con.close()
    return res, 1 if a.action == "check" and not res.get("ok") else 0


def _console_json(a):
    res, code = _console_result(a)
    if a.action == "bases":
        res = {"bases": {name: {"connected": item.get("connected", False)}
                          for name, item in res.get("bases", {}).items()}}
    return code, reasons.command_result("console", code, res)


def cmd_tj(a):
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        res = techlog.report(a.paths, a.report, a.mode, a.value, a.sort, a.top, a.process,
                             datetime.fromisoformat(a.since) if a.since else None,
                             datetime.fromisoformat(a.until) if a.until else None)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else techlog.format_report(res))
    return 0


def cmd_scaffold(a):
    repo = Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
    cfg = config.load(repo)
    src = a.source or cfg["project"]["source"]
    env = cfg["environment"]
    base = a.base or cfg.get("hook", {}).get("base", "origin/master")
    head = a.head or gitdiff.WORKTREE
    plan = collect_mod.collect(repo, base, head, src, env["contexts"], env["ordinaryApplication"])
    if a.dry_run:
        preview = scaffold.preview(repo, plan, cfg, a.extension, not a.no_draft)
        print("План заготовок (без изменений):")
        print(json.dumps(preview, ensure_ascii=False, indent=1))
        return 0

    def read_head(rel):
        return gitdiff.show(repo, head, f"{src}/{rel}")

    parent = cfg["project"].get("parent")
    config_root = (repo / parent) if parent else repo / src
    done = scaffold.scaffold(repo, plan, cfg, read_head, config_root, a.extension, not a.no_draft)
    if not done:
        print("заготовки не нужны: у изменённых методов есть тесты (или методы не изменены)")
        return 0
    for d in done:
        print(f"  {d['file']}: {d['test']} [{d['tag']}]" + (" — черновик" if d["draft"] else ""))
    print(f"заготовок: {len(done)}; тест помечен пропущенным (ЮТест.Пропустить), пока не написан")
    return 0


def cmd_collect(a):
    repo = _project(a)
    cfg = config.load(repo)
    source_dir = a.source or cfg["project"]["source"]
    env = cfg["environment"]
    head = gitdiff.WORKTREE if a.worktree else a.head
    try:
        # флаги заимствованных общих модулей; нет — не ошибка collect
        parent = config.parent_dir(repo, cfg, getattr(a, "parent", None))
    except InfraError:
        parent = None
    if a.dir:
        plan = collect_mod.collect_dirs(a.base_dir, a.dir, env["contexts"], env["ordinaryApplication"], parent)
    else:
        plan = collect_mod.collect(repo, a.base, head, source_dir, env["contexts"], env["ordinaryApplication"], parent)
    config.detect_extension(cfg, Path(a.dir) if a.dir else repo / source_dir)
    plan["notes"] = pipeline.plan_notes(cfg, getattr(a, "parent", None))      # как в gate
    plan["notes"] += pipeline.resolve_queries(plan, repo, cfg, workspace.run_dir(cfg, repo),
                                              log=lambda m: print(m, file=sys.stderr))
    text = collect_mod.dumps(plan)
    log = sys.stdout
    if a.out == "-":
        sys.stdout.write(text)
        log = sys.stderr                      # stdout — только план: collect --out - | jq
    else:
        out = Path(a.out) if a.out else workspace.run_dir(cfg, repo) / "otk.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"план: {out}")
    mods = plan["modules"]
    if not mods and not plan["removedModules"] and not plan["metadata"]:
        print("нечего проверять: в выгрузке нет изменений", file=log)
    else:
        print(f"модулей: {len(mods)}, удалено модулей: {len(plan['removedModules'])}, "
              f"файлов метаданных: {len(plan['metadata'])}, запросов: {len(plan['queries'])} "
              f"(неполных: {sum(1 for q in plan['queries'] if not q['complete'])})", file=log)
        for m in mods:
            names = ", ".join(x["name"] for x in m["changedMethods"]) or "—"
            print(f"  {m['status']} {m['owner']} [{', '.join(m['contexts'])}]: {names}", file=log)
    return 0


def _plan_path(a, run_dir):
    """План шага static или run: --plan или otk.json каталога прогона; нет — сбой одной строкой (no_plan)."""
    plan_path = Path(a.plan) if a.plan else run_dir / "otk.json"
    if not plan_path.is_file():
        raise InfraError(f"нет плана {plan_path}: сначала otk collect или gate", code="no-plan")
    return plan_path


def cmd_static(a):
    repo = _project(a)
    cfg = _cfg(a, repo)
    run_dir = workspace.run_dir(cfg, repo)
    plan_path = _plan_path(a, run_dir)
    config.detect_extension(cfg, workspace.plan_root(repo, plan_path, cfg))
    if cfg["project"].get("extension") and not cfg["project"].get("parent"):
        print(config.NO_PARENT, file=sys.stderr)
    result = pipeline.static_step(repo, cfg, run_dir, plan_path)
    findings, an_info, deps = result["findings"], result["analyzerInfo"], result["dependents"]
    src = json.loads(plan_path.read_text(encoding="utf-8"))["source"]
    out = plan_path.with_name("static.json")
    t1, t2, t3 = result["index"], result["checks"], result["analyzers"]
    fails = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warning"]
    if an_info.get("error") or an_info.get("skipped"):
        print(f"анализатор BSL не выполнен: {an_info.get('error') or an_info.get('skipped')}", file=sys.stderr)
    if result["apiInfo"].get("skipped"):
        print(f"методы платформы не проверены: {result['apiInfo']['skipped']}", file=sys.stderr)
    for note in result["apiInfo"].get("notes", []):
        print(f"справка платформы: {note}", file=sys.stderr)
    print(f"статика: индекс {t1:.1f} c, проверки {t2:.1f} c, BSL LS {t3:.1f} c; ошибок {len(fails)}, "
          f"предупреждений {len(warns)}, зависимых модулей {len(deps)} -> {out}")
    for f in fails + warns:
        print(f"  {f['severity']:4} {src}/{f['module']}:{f['line']}: {f['message']}")
    return 1 if fails else 0


def cmd_build(a):
    if getattr(a, "dry_run", False):
        if getattr(a, "json", False):
            return _json_out(a, _build_dry_run_json,
                             lambda text, code: reasons.command_error("build", text, code))
        result = _build_plan(a)
        print("План сборки (без изменений):")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if getattr(a, "json", False):
        return _json_out(a, _build_json, lambda text, code: reasons.command_error("build", text, code))
    code, result, logs = _build_data(a)
    for line in logs:
        print(line)
    return code


def _build_json(a):
    code, result, logs = _build_data(a)
    for line in logs:
        print(reasons.public_text(line))
    return code, reasons.command_result("build", code, result)


def _build_dry_run_json(a):
    """build --dry-run --json: тот же конверт, что у сборки, но build — план, а не выполненный шаг."""
    plan = _build_plan(a)
    return 0, reasons.command_result("build", 0,
                                    {"revision": plan["revision"], "origin": plan["origin"],
                                     "build": {"dryRun": True, "action": plan["action"], "source": plan["source"],
                                               "files": plan["files"], "createDatabase": plan["createDatabase"],
                                               "operations": plan["operations"]},
                                     "errors": []})


def _build_plan(a):
    repo = _project(a)
    cfg = _cfg(a, repo)
    run_dir = workspace.run_dir(cfg, repo)
    env = cfg["environment"]
    config.detect_extension(cfg, workspace.plan_root(repo, a.plan, cfg) if a.plan else repo / cfg["project"]["source"])
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8")) if a.plan else None
    origin = f"план {a.plan}" if a.plan else a.head or "HEAD"
    # копия ИБ — так же, как её видит сборка (Platform): /F<каталог> в connection тоже даёт каталог,
    # иначе dry-run читал бы состояние не там и показывал загрузку при up-to-date
    ib = ib_path(env)
    create_ib = False if a.assume_current else build.create_ib_plan(
        ib, env.get("connection"), cfg["project"].get("extension"))
    if plan and plan.get("headDir"):
        revision = None
        source = plan["source"]
        if a.assume_current:
            action, files = "mark-current", []
        else:
            files = None if a.full or create_ib else build.plan_load_dirs(plan)
            action = "full" if files is None else "up-to-date" if not files else "partial"
    else:
        revision = (plan.get("headSha") if plan else None) or gitdiff.rev(repo, a.head or "HEAD")
        source = plan["source"] if plan else cfg["project"]["source"]
        state = None if create_ib else build.load_state(run_dir, ib)
        tree = build.tree_hash(repo, revision, source)
        if a.assume_current:
            action, files = "mark-current", []
        elif state and state.get("ib") == str(ib) and state.get("tree") == tree and not a.full:
            action, files = "up-to-date", []
        else:
            # способ загрузки — тем же расчётом, что у сборки (build.plan_from_state): без записи в ИБ и каталог прогона
            files, _modules, _lost = build.plan_from_state(repo, source, repo, state, revision, ib, a.full,
                                                           cfg["project"].get("extension"))
            action = "full" if files is None else "up-to-date" if not files else "partial"
    operations = []
    if create_ib:
        operations.append("создать файловую ИБ")
    if action == "mark-current":
        operations.append("записать состояние текущей редакции без загрузки")
    elif action != "up-to-date":
        operations += ["загрузить выгрузку: " + ("полностью" if files is None else f"частично ({len(files)} файлов)"),
                       "обновить конфигурацию базы данных"]
    return {"revision": revision, "origin": origin, "source": source, "action": action,
            "files": files, "createDatabase": create_ib, "operations": operations}


def _build_data(a):
    repo = _project(a)
    cfg = _cfg(a, repo)
    run_dir = workspace.run_dir(cfg, repo)
    config.detect_extension(cfg, workspace.plan_root(repo, a.plan, cfg) if a.plan else repo / cfg["project"]["source"])
    # редакция — HEAD (или --head), а не последнего плана в каталоге прогона: тот мог остаться от прогона другой
    # редакции, и копия ИБ получила бы чужой код; план — только явно указанный
    if a.plan:
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
        if plan.get("headDir"):                   # план без git: контракт — в копии ИБ «было» (baseDir)
            logs = [f"сборка: каталог {plan['headDir']} (план {a.plan})"]
            try:
                res = build.build_dirs(plan, Platform(cfg["environment"], run_dir), run_dir,
                                       extension=cfg["project"].get("extension"), full=a.full,
                                       assume_current=a.assume_current)
            except build.LoadError as e:
                logs.extend(f"{rel}: {message}" for rel, message in e.errors)
                return 1, {"revision": None, "origin": f"план {a.plan}", "build": None,
                           "errors": [{"file": rel, "message": message} for rel, message in e.errors]}, logs
            logs.append(f"сборка: {res}")
            return 0, {"revision": None, "origin": f"план {a.plan}", "build": res, "errors": []}, logs
        head, origin = plan.get("headSha") or gitdiff.rev(repo, "HEAD"), f"план {a.plan}"
    else:
        head, origin = gitdiff.rev(repo, a.head or "HEAD"), a.head or "HEAD"
        plan = {"headSha": head, "source": cfg["project"]["source"]}
    logs = [f"сборка: редакция {head[:10]} ({origin})"]
    try:
        res = build.build(repo, head, plan["source"], workspace.tree_root(repo, plan, run_dir),
                          Platform(cfg["environment"], run_dir), run_dir, extension=cfg["project"].get("extension"),
                          full=a.full, assume_current=a.assume_current)
    except build.LoadError as e:
        logs.extend(f"{plan['source']}/{rel}: {message}" for rel, message in e.errors)
        errors = [{"file": f"{plan['source']}/{rel}", "message": message} for rel, message in e.errors]
        return 1, {"revision": head, "origin": origin, "build": None, "errors": errors}, logs
    logs.append(f"сборка: {res}")
    return 0, {"revision": head, "origin": origin, "build": res, "errors": []}, logs


def cmd_run(a):
    repo = _project(a)
    cfg = _cfg(a, repo)
    run_dir = workspace.run_dir(cfg, repo)
    plan_path = _plan_path(a, run_dir)
    config.detect_extension(cfg, workspace.plan_root(repo, plan_path, cfg))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if stale := report.plan_version_note(plan):
        print(stale, file=sys.stderr)
        return 2
    static, stale = report.step_for(plan, plan_path.with_name("static.json"))   # зависимые модули — от static
    if stale:
        print(f"{stale}: зависимые модули не проверяются", file=sys.stderr)
    out = runner.run(repo, plan, cfg, run_dir, workspace.tree_root(repo, plan, run_dir), static=static)
    res = out["results"]
    fails = [r for r in res if r["outcome"] in model.FAIL]
    warns = [r for r in res if r["outcome"] == "runtime-error"]
    ok = sum(1 for r in res if r["outcome"] == "ok")
    uncovered = [r for r in res if r["kind"] == "uncovered"]
    print(f"пробы: успешно {ok}, ошибок {len(fails)}, предупреждений {len(warns)}, не покрыто {len(uncovered)}; "
          f"время {out['timings']}")
    for n in out["notes"]:
        print(f"  примечание: {n}")
    for r in fails + warns:
        where = f"{plan['source']}/{r['module']}:{r['line']}" if r.get("module") else (r.get("owner") or "")
        print(f"  {r['outcome']:13} [{r['executor']}/{r['context']}] {where}: {r['message']}")
    return 1 if fails else 0


def _open_kind(a, cfg):
    """--open [html|md] или [report] open: какую сводку открыть после прогона ('' — никакую)."""
    kind = a.open if a.open is not None else cfg.get("report", {}).get("open") or ""
    if kind and kind not in report_html.OPEN_KINDS:
        raise ValueError(f"report.open = {kind}: допустимо html или md")
    return kind


def cmd_report(a):
    if a.json:
        return _json_out(a, _report_json, reasons.report_error)
    summary, run_dir, repo = _report_data(a)
    if summary is None:
        print(f"нет плана {run_dir / 'otk.json'}: сначала otk collect или gate", file=sys.stderr)
        return 2
    _report_output(summary, run_dir, repo, a)
    return summary["code"]


def _report_data(a):
    repo = _project(a)
    run_dir = workspace.run_dir(config.load(repo), repo)
    if a.run_dir:
        # каталог прогона или, как у gate, корень прогонов: тогда в нём — каталог прогона проекта
        given = Path(a.run_dir)
        nested = given / run_dir.name
        run_dir = nested if not (given / "otk.json").exists() and (nested / "otk.json").exists() else given
    if not (run_dir / "otk.json").exists():
        return None, run_dir, repo
    plan = json.loads((run_dir / "otk.json").read_text(encoding="utf-8"))
    summary = report.from_files(run_dir, tree=lambda: workspace.tree_root(repo, plan, run_dir))
    head_dir = plan.get("headDir")
    output.write(summary, run_dir, Path(head_dir) if head_dir else repo)
    return summary, run_dir, repo


def _report_output(summary, run_dir, repo, a):
    md = report.console(summary, a.full)
    print(md if not a.quiet else md.splitlines()[0])
    print(f"сводка: {run_dir / 'summary.md'} (HTML: {run_dir / 'summary.html'})")
    kind = _open_kind(a, config.load(repo))
    if kind:
        report_html.open_summary(run_dir, kind)


def _report_json(a):
    summary, run_dir, repo = _report_data(a)
    if summary is None:
        raise InfraError(f"нет плана {run_dir / 'otk.json'}: сначала otk collect или gate", code="no-plan")
    _report_output(summary, run_dir, repo, a)
    return summary["code"], reasons.report_result(summary, run_dir)


def cmd_gate(a):
    if a.json:
        return _json_out(a, _gate_json, reasons.gate_error)
    if a.designer and a.static_only:
        print(f"otk gate: {DESIGNER_STATIC}", file=sys.stderr)
        return 2
    return _gate_text(a)


def _internal(e):
    return f"внутренняя ошибка ОТК: {type(e).__name__}: {e}"


def _json_out(a, run, error):
    """--json: журнал и всё прочее — в stderr, в stdout — только итоговый объект run(a) -> (код, объект).
    Объект печатается всегда: прогон оборвался — error(текст, код причины) с кодом 2."""
    out = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            code, res = run(a)
        except USER_ERRORS as e:
            print(f"ошибка: {reasons.public_text(str(e))}")
            code, res = 2, error(str(e), getattr(e, "code", None))
        except Exception as e:  # noqa: BLE001 - сбой самой ОТК: тоже объект, а не трассировка вместо JSON
            print(reasons.public_text(traceback.format_exc()), file=sys.stderr, end="")
            code, res = 2, error(_internal(e), "internal")
    print(json.dumps(res, ensure_ascii=False, indent=1), file=out)
    return code


def _gate_json(a):
    if a.designer and a.static_only:
        raise InfraError(DESIGNER_STATIC, code="config-invalid")
    repo = _project(a)
    cfg = _cfg(a, repo)
    kind = _open_kind(a, cfg)                 # неверный report.open — до прогона, а не после
    summary, run_dir = _gate_run(a, repo, cfg)
    if kind:
        report_html.open_summary(run_dir, kind)
    return summary["code"], reasons.gate_result(summary, run_dir)


def _gate_run(a, repo, cfg):
    """Прогон gate по ключам: (сводка, каталог прогона); журнал — в stdout."""
    if not a.vendor:
        config.detect_extension(cfg, Path(a.dir) if a.dir else repo / (a.source or cfg["project"]["source"]))
    run_dir = workspace.run_dir(cfg, repo)
    opts = pipeline.GateOptions(a.base, a.head, a.worktree, a.source, head_dir=a.dir, base_dir=a.base_dir,
                                vendor=a.vendor, static_only=a.static_only, fast=a.fast,
                                all_scenarios=a.all_scenarios, designer=a.designer)
    return pipeline.gate_run(repo, cfg, run_dir, opts, log=lambda m: print(m, flush=True)), run_dir


def _gate_text(a):
    repo = _project(a)
    cfg = _cfg(a, repo)
    kind = _open_kind(a, cfg)
    summary, run_dir = _gate_run(a, repo, cfg)
    md = report.console(summary, a.full)
    print(md if not a.quiet else md.splitlines()[0])
    if a.problems:
        root = Path(a.dir).resolve() if a.dir else Path(summary["head"]) if a.vendor else repo
        for line in report.problems(summary, root):
            print(line)
    print(f"сводка: {run_dir / 'summary.md'} (HTML: {run_dir / 'summary.html'})")
    if kind:
        report_html.open_summary(run_dir, kind)
    return summary["code"]


def cmd_controls(a):
    repo = Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
    cfg = config.load(repo)
    config.detect_extension(cfg, repo / cfg["project"]["source"])     # иначе расширение грузится как конфигурация
    run_dir = workspace.run_dir(cfg, repo)
    if a.analyzers:
        return _controls_analyzers(a, repo, cfg, run_dir)
    rows, _, table = controls.run(repo, cfg, run_dir, parity=a.parity or bool(a.doc), session=not a.no_session,
                                  log=lambda m: print(m, flush=True))
    print(table)
    if a.doc:
        doc = Path(a.doc) if Path(a.doc).is_absolute() else repo / a.doc
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text((run_dir / "vs-designer.md").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"таблица паритета: {doc}")
    missed = [d.id for d, ok, _ in rows if not ok]
    print("пропущены: " + ", ".join(missed) if missed else "все дефекты пойманы ожидаемыми шагами")
    return 1 if missed else 0


def _controls_analyzers(a, repo, cfg, run_dir):
    """controls --analyzers: таблица «ОТК и анализаторы BSL»; код 2 — не работал ни один анализатор (окружение)."""
    if a.parity:
        print("otk controls: --analyzers несовместим с --parity", file=sys.stderr)
        return 2
    rows, status, table = controls.run_analyzers(repo, cfg, run_dir, session=not a.no_session,
                                                 log=lambda m: print(m, flush=True))
    if all(status.values()):
        print("otk controls: ни один анализатор не работал — " + "; ".join(status.values()), file=sys.stderr)
        return 2
    print(table)
    if a.doc:
        doc = Path(a.doc) if Path(a.doc).is_absolute() else repo / a.doc
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text((run_dir / "vs-analyzers.md").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"таблица анализаторов: {doc}")
    missed = [d.id for d, h in rows if d.expect == controls.ANALYZER_EXPECT and not (h["bslls"] or h["bsl-analyzer"])]
    if missed:
        print("дефекты семантики не поймал ни один анализатор: " + ", ".join(missed))
    return 0


def cmd_designer_check(a):
    repo = Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
    cfg = config.load(repo)
    run_dir = workspace.run_dir(cfg, repo)
    if a.dry_run:
        baseline = designer_check.baseline_of(repo, cfg)
        operations = ["загрузить HEAD в копию ИБ"]
        if not a.no_control:
            operations.append("проверить отрицательным контролем, что Конфигуратор видит ошибки")
        operations += ["проверить модули Конфигуратором", "записать отчёты в каталог прогона"]
        if a.update_baseline:
            operations.append("перезаписать baseline найденными ошибками")
        plan = {"source": cfg["project"]["source"], "runDir": str(run_dir),
                "control": not a.no_control, "operations": operations,
                "baseline": {"path": str(baseline), "update": a.update_baseline}}
        print("План designer-check (без изменений):")
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0
    summary = designer_check.run(repo, cfg, run_dir, control=not a.no_control, log=lambda m: print(m, flush=True),
                            update_baseline=a.update_baseline)
    print(designer_check.markdown(summary))
    print(f"сводка: {run_dir / 'designer-check.md'}")
    return summary["code"]


def _doctor_project(cfg, root):
    """Строки doctor о проекте: какая выгрузка, расширение ли, есть ли основная конфигурация."""
    project = cfg["project"]
    if layout.detect(root) is None:
        return [f"Выгрузка: {root} — нет файла конфигурации (Configuration.xml или Configuration/Configuration.mdo; "
                "укажите --dir или --source)", ""]
    lines = [f"Выгрузка: {root}"]
    if project.get("extension"):
        how = "распознано по файлу конфигурации" if project.get("extensionDetected") else "из настроек"
        lines.append(f"  расширение «{project['extension']}» ({how})")
        lines.append(f"  основная конфигурация: {project['parent']}" if project.get("parent") else
                     f"  основная конфигурация: не задана — {config.NO_PARENT}")
    else:
        lines.append("  основная конфигурация (не расширение)")
    return lines + [""]


def _env_args(p, parent=True):
    """Ключи окружения: всё, что иначе задаётся в otk.toml / otk.local.toml (аргумент важнее)."""
    e = p.add_argument_group("окружение (важнее otk.toml и otk.local.toml; пути — от текущего каталога)")
    if parent:
        e.add_argument("--parent", help="выгрузка основной конфигурации (для расширения): без неё ссылки на "
                                        "объекты и методы основной конфигурации не проверяются")
    e.add_argument("--extension", help="имя расширения в ИБ (по умолчанию — из файла конфигурации выгрузки)")
    e.add_argument("--ib", help="СВОЯ файловая копия ИБ (ОТК её меняет); без --ib и --connection — "
                                "только статика")
    e.add_argument("--connection", help="ИЛИ рабочая база («/Fкаталог», «/Sсервер\\база»): после проверки "
                                        "возвращается как была")
    e.add_argument("--user", help="пользователь ИБ")
    e.add_argument("--platform", help="каталог bin платформы 1С (по умолчанию — самая новая установленная)")
    e.add_argument("--yaxunit", help="расширение YAxUnit (.cfe): клиентские пробы, сценарии, формы; "
                                     "ОТК его не скачивает")
    e.add_argument("--bslls", help="BSL Language Server (по умолчанию OTK_BSLLS, PATH или расширение VS Code)")
    e.add_argument("--bsl-analyzer", help="bsl-analyzer (по умолчанию OTK_BSL_ANALYZER или PATH)")
    e.add_argument("--analyzer", choices=("bslls", "bsl-analyzer", "both"),
                   help="анализатор BSL проверки analyzers (environment.analyzer; по умолчанию bslls — BSL LS)")
    e.add_argument("--edtcli", help="1cedtcli (исходники EDT грузятся через экспорт; по умолчанию OTK_EDTCLI, "
                                    "установка 1C:EDT пользователя или PATH)")
    e.add_argument("--run-dir", help="каталог прогонов (по умолчанию %%TEMP%%/otk)")
    e.add_argument("--min-platform", help="минимальная платформа проекта (8.3.24): предупреждать только о методах "
                                          "и конструкторах новее неё (environment.minPlatform)")
    e.add_argument("--no-compat-warnings", action="store_true",
                   help="без предупреждений «новее режима совместимости» своей линейки (environment.compatWarnings)")
    e.add_argument("--set", action="append", metavar="РАЗДЕЛ.КЛЮЧ=ЗНАЧЕНИЕ",
                   help="любая настройка otk.toml, например checks.analyzers=off или smoke.timeout=120; "
                        "можно несколько")


def cmd_doctor(a):
    if a.json:
        return _json_out(a, _doctor_json, reasons.doctor_error)
    cfg, root = _doctor_setup(a)
    lines, code = doctor.doctor(cfg)
    print("\n".join(_doctor_project(cfg, root) + lines))
    return code


def _doctor_setup(a):
    """Настройки и каталог выгрузки для doctor."""
    repo = _project(a)
    cfg = _cfg(a, repo)
    root = Path(a.dir) if a.dir else repo / (a.source or cfg["project"]["source"])
    config.detect_extension(cfg, root)
    return cfg, root


def _doctor_json(a):
    cfg, root = _doctor_setup(a)
    project = cfg["project"]
    info = {"root": str(root), "found": layout.detect(root) is not None, "extension": project.get("extension"),
            "parent": project.get("parent")}
    if not info["found"]:
        info.update(reasonCode="no-source", remediation=reasons.remediation("no-source"))
    elif info["extension"] and not info["parent"]:
        info.update(reasonCode="no-parent", remediation=reasons.remediation("no-parent"))
    res = doctor.result(cfg)
    return res["code"], reasons.doctor_result(res, info)


def cmd_availability(a):
    repo = Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
    cfg = config.load(repo)
    parent = config.parent_dir(repo, cfg)
    config_root = parent or repo / cfg["project"]["source"]
    if a.dry_run:
        env = cfg["environment"]
        bin_dir = env.get("platform") or find_platform()
        # имя таблицы — как у availability.build: три компонента версии (8.3.27)
        version = availability.table_version(platform_version(bin_dir)) if bin_dir else None
        out_dir = Path(a.out or contexts.user_tables_dir())
        plan = {"platform": version, "extension": availability.EXT, "configurationRoot": str(config_root),
                "output": str(out_dir / f"{version}.json") if version else None,
                "operations": ["создать временное расширение", "загрузить его в копию ИБ",
                               "проверить доступность API по контекстам", "удалить временное расширение"]}
        if version:
            plan["operations"].append(f"записать таблицу доступности в {plan['output']}")
        print("План availability build (без изменений):")
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0 if version else 2
    out = availability.build(Platform(cfg["environment"], workspace.run_dir(cfg, repo)), config_root,
                      a.out or contexts.user_tables_dir(), log=lambda m: print(m, flush=True))
    print(f"записано: {out}")
    return 0


def cmd_platform_help(a):
    """otk platform-help status | build: индекс справки синтакс-помощника; справки нет — код 2 (окружение)."""

    if getattr(a, "json", False):
        if a.action != "status":
            raise ValueError("--json поддерживается для platform-help status")
        return _json_out(a, _platform_help_json,
                         lambda text, code: reasons.command_error("platform-help", text, code))

    env = config.load(Path(a.repo))["environment"]
    syntax.configure(env)
    if a.dry_run and a.action == "build":
        src = a.from_dir or a.platform or env.get("platform")
        if not src:
            plats = syntax.installed_platforms()
            src = list(plats.values())[-1] if plats else None
        version = a.version or (syntax.index.version_of_dir(src) if src else None)
        plan = {"source": str(src) if src else None, "version": version,
                "index": str(Path(syntax.cache_dir()) / f"{version}.db") if version else None,
                "operations": ["прочитать файлы справки .hbk", "построить индекс SQLite в кэше пользователя"]}
        print("План platform-help build (без изменений):")
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0 if src and version else 2
    if a.action == "status":
        rows = syntax.status()
        print(f"кэш: {syntax.cache_dir()}; helpAuto = {'true' if syntax.index.AUTO else 'false'}")
        for r in rows:
            print(f"  {r['version']}: индекс {r['index']}" + (f", {r['entries']} записей" if r["entries"] else "")
                  + (f" — {r['source']}" if r["source"] else ""))
        if not rows:
            print("справки нет: " + syntax.build_command())
        return 0 if rows else 2
    src = a.from_dir or a.platform or env.get("platform")
    if not src:
        plats = syntax.installed_platforms()
        if not plats:
            print("ошибка: нет установленной платформы со справкой: " + syntax.build_command(), file=sys.stderr)
            return 2
        src = list(plats.values())[-1]
    try:
        v, path, n = syntax.build_from(src, a.version)
    except (RuntimeError, OSError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    print(f"индекс справки {v}: {n} записей -> {path}")
    return 0


def _platform_help_json(a):
    env = config.load(Path(a.repo))["environment"]
    syntax.configure(env)
    rows = syntax.status()
    result = {"action": "status", "cacheDir": str(syntax.cache_dir()), "helpAuto": syntax.index.AUTO, "rows": rows}
    if not rows:
        text = "справки нет: " + syntax.build_command()
        return 2, reasons.command_result("platform-help", 2, result, reasons.item(text, "infra", "no-help"))
    return 0, reasons.command_result("platform-help", 0, result)


def cmd_mcp(a):
    mcp.serve(a.idle)
    return 0


def cmd_ci(a):
    return ci_mod.main(a)


def _open_arg(p):
    p.add_argument("--open", nargs="?", const="html", choices=report_html.OPEN_KINDS,
                   help="открыть сводку после прогона: html (по умолчанию) — в браузере, md — в VS Code; "
                        "настройка [report] open")


def _add_collect(sub):
    c = sub.add_parser("collect", help="git diff -> план проверки otk.json")
    c.set_defaults(func=cmd_collect)
    c.add_argument("--repo", default=".")
    c.add_argument("--base", help="базовая ревизия (например, origin/master)")
    c.add_argument("--dir", help="без git: каталог выгрузки (файл конфигурации в корне, либо каталог проекта EDT); "
                                 "без --base-dir — все модули")
    c.add_argument("--base-dir", help="без git: выгрузка «было» — сравнить с --dir")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--head", default="HEAD")
    g.add_argument("--worktree", action="store_true", help="сравнить с рабочим деревом")
    c.add_argument("--source", help="каталог выгрузки относительно корня репозитория")
    c.add_argument("--out", help="файл плана ('-' — stdout)")


def _add_static(sub):
    s = sub.add_parser("static", help="статические проверки плана без базы")
    s.set_defaults(func=cmd_static)
    s.add_argument("--repo", default=".")
    s.add_argument("--plan", help="файл плана (по умолчанию otk.json в каталоге прогона)")
    _env_args(s)


def _add_build(sub):
    b = sub.add_parser("build", help="загрузить редакцию (HEAD, --head или --plan) в копию ИБ")
    b.set_defaults(func=cmd_build)
    b.add_argument("--repo", default=".")
    b.add_argument("--head", help="ревизия (по умолчанию HEAD)")
    b.add_argument("--plan", help="собрать редакцию этого плана (otk.json)")
    b.add_argument("--full", action="store_true", help="полная загрузка")
    b.add_argument("--assume-current", action="store_true", help="считать, что ИБ уже соответствует редакции плана")
    b.add_argument("--json", action="store_true", help="итог одним объектом JSON; журнал — в stderr")
    b.add_argument("--dry-run", action="store_true", help="показать план загрузки без изменений ИБ и файлов")
    _env_args(b, parent=False)


def _add_run(sub):
    r = sub.add_parser("run", help="пробы в копии ИБ по плану")
    r.set_defaults(func=cmd_run)
    r.add_argument("--repo", default=".")
    r.add_argument("--plan")
    _env_args(r)


FULL_HELP = (f"в консоль вся сводка; без ключа при находках больше {report.BRIEF_OVER} — счётчики, частые причины "
             "и объекты с наибольшим числом ошибок (целиком — в summary.md)")


def _add_report(sub):
    rp = sub.add_parser("report", help="сводка по файлам шагов каталога прогона (после collect/static/run); код 0/1/2")
    rp.set_defaults(func=cmd_report)
    rp.add_argument("--repo", default=".")
    rp.add_argument("--run-dir", help="каталог прогона или, как у gate, каталог прогонов (по умолчанию — "
                                      "каталог прогона проекта)")
    rp.add_argument("--json", action="store_true", help="итог одним объектом JSON; журнал — в stderr")
    rp.add_argument("--quiet", action="store_true", help="только строка вердикта")
    rp.add_argument("--full", action="store_true", help=FULL_HELP)
    _open_arg(rp)


def _add_gate(sub):
    gt = sub.add_parser("gate", help="вся проверка: collect -> static -> build/run -> сводка; код 0/1/2")
    gt.set_defaults(func=cmd_gate)
    gt.add_argument("--repo", default=".")
    gb = gt.add_mutually_exclusive_group(required=True)
    gb.add_argument("--base", help="базовая ревизия (например, origin/master)")
    gb.add_argument("--all", action="store_true", help="все модули выгрузки (для небольших проектов и сверки)")
    gb.add_argument("--dir", help="без git: каталог выгрузки (файл конфигурации в корне, либо каталог проекта EDT); "
                                  "без --base-dir — все модули и полная загрузка в копию ИБ. Сводка, план и файлы "
                                  "шагов (с фрагментами кода) — в каталоге прогона: --run-dir, по умолчанию "
                                  "%%TEMP%%/otk/<имя>-<хеш пути>")
    gb.add_argument("--vendor", action="store_true",
                    help="разовая проверка доработок ИБ (--ib или --connection) относительно её конфигурации "
                         "поставщика: /CompareCfg и выгрузка ИБ — минуты (ERP: первый раз ~15 мин, дальше из кэша "
                         "быстрее); не для хука. Конфигурация должна быть на поддержке с разрешёнными изменениями")
    gt.add_argument("--base-dir", help="без git: выгрузка «было» — проверяются отличия --dir от неё. КОНТРАКТ: копия "
                                       "ИБ уже содержит эту выгрузку (ОТК не сверяет) — в неё загружаются только "
                                       "отличия --dir; иначе пробы проверят не то. Нет уверенности — без --base-dir")
    gg = gt.add_mutually_exclusive_group()
    gg.add_argument("--head", default="HEAD")
    gg.add_argument("--worktree", action="store_true", help="проверить незакоммиченные изменения (git stash create)")
    gt.add_argument("--source")
    gt.add_argument("--static-only", action="store_true", help="без проб в базе")
    gt.add_argument("--fast", action="store_true", help="не запускать пробы, если статика нашла ошибки")
    gt.add_argument("--designer", action="store_true",
                    help="плюс проверка всех модулей Конфигуратором в той же копии ИБ (отрицательный контроль, базовая "
                         "линия designer-check): одна сводка и код выхода; минуты-часы — режим для CI, не для хука")
    gt.add_argument("--all-scenarios", action="store_true",
                    help="все сценарные тесты, а не только по изменённым методам")
    gt.add_argument("--problems", action="store_true", help="напечатать находки в формате Problems VS Code")
    gt.add_argument("--quiet", action="store_true", help="только строка вердикта (и --problems)")
    gt.add_argument("--json", action="store_true",
                    help="для агентов: в stdout — только итог одним объектом JSON (schema/gate-result.schema.json, "
                         "причины «не проверено» с reasonCode), журнал — в stderr; код выхода тот же")
    gt.add_argument("--full", action="store_true", help=FULL_HELP)
    _open_arg(gt)
    _env_args(gt)


def _add_controls(sub):
    ct = sub.add_parser("controls", help="контрольные дефекты: каждый должен быть пойман ожидаемым шагом")
    ct.set_defaults(func=cmd_controls)
    ct.add_argument("--repo", default=".")
    ct.add_argument("--parity", action="store_true",
                    help="плюс проверка Конфигуратором: /CheckModules и /CheckConfig (таблица паритета)")
    ct.add_argument("--analyzers", action="store_true",
                    help="статикой с обоими анализаторами BSL: кто что ловит (таблица «ОТК и анализаторы BSL»)")
    ct.add_argument("--no-session", action="store_true", help="без дефекта модуля сеанса")
    ct.add_argument("--doc", metavar="ФАЙЛ",
                    help="с паритетом: документ «ОТК и Конфигуратор» (условия, два эталона, сноски), "
                         "например docs/vs-designer.md; с --analyzers — docs/vs-analyzers.md")


def _add_designer_check(sub):
    fc = sub.add_parser("designer-check",
                        help="проверка всех модулей Конфигуратором с отрицательным контролем; код 0/1/2")
    fc.set_defaults(func=cmd_designer_check)
    fc.add_argument("--repo", default=".")
    fc.add_argument("--no-control", action="store_true", help="без отрицательного контроля")
    fc.add_argument("--update-baseline", action="store_true", help="принять текущие ошибки базовой линией")
    fc.add_argument("--dry-run", action="store_true", help="показать план проверки и baseline без изменений")


def _add_ci(sub):
    ci = sub.add_parser("ci", help="запуск в CI: gate | designer-check + артефакты (docs/ci.md)")
    ci.set_defaults(func=cmd_ci)
    ci.add_argument("action", choices=["gate", "designer-check"])
    ci.add_argument("--repo", help="checkout проекта (по умолчанию GITHUB_WORKSPACE/<имя из GITHUB_REPOSITORY>)")
    ci.add_argument("--base", help="(gate) базовая ревизия PR, например origin/master; по умолчанию — целевая "
                                   "ветка запроса из переменных CI; вне запроса — обязателен")
    ci.add_argument("--designer", action="store_true", help="(gate) плюс все модули Конфигуратором, одна сводка")
    ci.add_argument("--no-control", action="store_true", help="(designer-check) без отрицательного контроля")
    ci.add_argument("--artifacts", metavar="КАТАЛОГ",
                    help="сложить артефакты в каталог — его забирает система CI (GitHub Actions, GitLab CI, Jenkins); "
                         "без ключа — загрузка протоколом Forgejo Actions")


def _add_pt(sub):
    pt = sub.add_parser("pt", help="хуки Platform Tools: pre (сохранить расширение) | post (проверить, откатить) | "
                                   "check (проверить в базе) | static (статика исходников) | "
                                   "cf-pre / cf-post (основная конфигурация: статика до загрузки, проверка после)")
    pt.set_defaults(func=pt_mod.main)
    pt.add_argument("action", choices=["pre", "post", "check", "static", "cf-pre", "cf-post"])
    pt.add_argument("--no-static", action="store_true", help="(post) без статики: загружен .cfe, а не исходники")
    pt.add_argument("--on-error", action="store_true", help="(post, cf-post) вызов из onError: загрузка не удалась")
    pt.add_argument("--repo", default=".")
    pt.add_argument("--args", help="строка аргументов vrunner (по умолчанию VRUNNER_ARGS)")


def _add_hook(sub):
    hk = sub.add_parser("hook", help="git-хук pre-push: install | uninstall | run (вызывает сам хук)")
    hk.set_defaults(func=hook.main)
    hk.add_argument("action", choices=["install", "uninstall", "run"])
    hk.add_argument("remote", nargs="?", help="(run) имя удалённого репозитория — передаёт git")
    hk.add_argument("url", nargs="?", help="(run) адрес удалённого репозитория — передаёт git")
    hk.add_argument("--repo", default=".")
    hk.add_argument("--force", action="store_true", help="(install) заменить чужой хук pre-push")
    hk.add_argument("--dry-run", action="store_true", help="показать план установки/удаления без изменений")


def _add_scaffold(sub):
    sf = sub.add_parser("scaffold", help="заготовки сценарных тестов для изменённых методов без теста")
    sf.set_defaults(func=cmd_scaffold)
    sf.add_argument("--repo", default=".")
    sf.add_argument("--base", help="базовая ревизия (по умолчанию [hook] base или origin/master)")
    sf.add_argument("--head", help="проверяемая ревизия (по умолчанию — рабочее дерево с незакоммиченным)")
    sf.add_argument("--source")
    sf.add_argument("--extension", help="тестовое расширение, если их несколько (имя)")
    sf.add_argument("--no-draft", action="store_true", help="без черновика ([scaffold] draft) — только каркас")
    sf.add_argument("--dry-run", action="store_true", help="показать план тестовых файлов без изменений")


def _add_console(sub):
    cs = sub.add_parser("console", help="консоль копии ИБ: query | check | metadata | eventlog | bases (только чтение)")
    cs.set_defaults(func=cmd_console)
    cs.add_argument("action", choices=["query", "check", "metadata", "bases", "eventlog"])
    cs.add_argument("text", nargs="?", help="текст запроса (- — из stdin) или полное имя объекта метаданных")
    cs.add_argument("--ib", help="копия ИБ: псевдоним из файла баз или «/Fкаталог» / «/Sсервер\\база»")
    cs.add_argument("--params", help="параметры запроса JSON-объектом")
    cs.add_argument("--limit", type=int, default=200)
    cs.add_argument("--filter", help="(eventlog) отбор JSON: from, to, level, user, event, metadata, data, …")
    cs.add_argument("--group", help="(eventlog) группировка: user,event,metadata,level,date:день …")
    cs.add_argument("--json", action="store_true", help="итог одним объектом JSON; журнал — в stderr")


def _add_tj(sub):
    tj = sub.add_parser("tj", help="анализ технологического журнала: "
                                   "calls | queries | sdbl | errors | locks | timeouts | events")
    tj.set_defaults(func=cmd_tj)
    tj.add_argument("report", choices=["calls", "queries", "sdbl", "errors", "locks", "timeouts", "events"])
    tj.add_argument("paths", nargs="+", help="каталоги ТЖ (с подкаталогами процессов) или файлы .log")
    tj.add_argument("--mode", help="queries/sdbl: sql | sql+context | context; errors: descr | descr+context")
    tj.add_argument("--value", default="duration",
                    help="calls: duration | memory | memorypeak | inbytes | outbytes | cpu")
    tj.add_argument("--sort", choices=["sum", "count", "mean", "max"],
                    help="по умолчанию sum, у errors/timeouts — count")
    tj.add_argument("--top", type=int, default=100)
    tj.add_argument("--process", help="только каталоги процессов с этим началом имени (rphost)")
    tj.add_argument("--from", dest="since", help="с (ГГГГ-ММ-ДДTЧЧ:ММ)")
    tj.add_argument("--to", dest="until", help="по (ГГГГ-ММ-ДДTЧЧ:ММ)")
    tj.add_argument("--json", action="store_true")


def _add_mcp(sub):
    mc = sub.add_parser("mcp", help="MCP-сервер консоли ИБ (stdio)")
    mc.set_defaults(func=cmd_mcp)
    mc.add_argument("--idle", type=int, default=120, help="через сколько секунд простоя отпускать базу")


def _add_doctor(sub):
    dr = sub.add_parser("doctor", help="окружение: что найдено, какие проверки доступны и что теряется")
    dr.set_defaults(func=cmd_doctor)
    dr.add_argument("--json", action="store_true",
                    help="итог одним объектом JSON (schema/doctor-result.schema.json): что чинить — по reasonCode")
    dr.add_argument("--repo", default=".")
    dr.add_argument("--dir", help="каталог выгрузки: распознать расширение и показать, что будет проверено")
    dr.add_argument("--source")
    _env_args(dr)


def _add_availability(sub):
    av = sub.add_parser("availability", help="build — таблица доступности методов платформы по контекстам "
                                             "(Конфигуратором на копии ИБ проекта) для своей платформы")
    av.set_defaults(func=cmd_availability)
    av.add_argument("action", choices=["build"])
    av.add_argument("--repo", default=".")
    av.add_argument("--out", help="каталог таблицы (по умолчанию — кэш пользователя)")
    av.add_argument("--dry-run", action="store_true", help="показать план без запуска платформы и записи таблицы")


def _add_platform_help(sub):
    hp = sub.add_parser("platform-help", help="справка платформы для platform-api: status — индексы в кэше, "
                                              "build — построить индекс (--platform <bin> или --from <каталог с .hbk>)")
    hp.set_defaults(func=cmd_platform_help)
    hp.add_argument("action", choices=["status", "build"])
    hp.add_argument("--repo", default=".", help="проект (environment.platform, helpDir, helpAuto из otk.toml)")
    hp.add_argument("--platform", help="каталог bin платформы (по умолчанию environment.platform или новейшая)")
    hp.add_argument("--from", dest="from_dir", help="каталог с копией shcntx_ru.hbk, shlang_ru.hbk, shquery_ru.hbk")
    hp.add_argument("--version", help="версия платформы, если не видна по имени каталога (8.3.27.2342)")
    hp.add_argument("--json", action="store_true", help="для status: итог одним объектом JSON")
    hp.add_argument("--dry-run", action="store_true", help="для build: показать источник и файл индекса без записи")


def _add_help(sub):
    hp = sub.add_parser("help", help="справка ОТК: otk help [команда] — то же, что otk -h или otk <команда> -h")
    hp.set_defaults(func=cmd_help)
    hp.add_argument("command", nargs="?", help="команда")


# подкоманды в порядке справки otk -h
COMMANDS = (_add_collect, _add_static, _add_build, _add_run, _add_report, _add_gate, _add_controls,
            _add_designer_check, _add_ci, _add_pt, _add_hook, _add_scaffold, _add_console, _add_tj, _add_mcp,
            _add_doctor, _add_availability, _add_platform_help, _add_help)


def build_parser():
    ap = argparse.ArgumentParser(prog="otk", description="Быстрая проверка изменений кода конфигурации 1С")
    ap.add_argument("--version", action="version", version=f"otk {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for add in COMMANDS:
        add(sub)
    return ap


def cmd_help(a):
    """otk help [команда]: справка argparse; неизвестная команда — код 2."""
    ap = build_parser()
    if not a.command:
        ap.print_help()
        return 0
    subs = next(x for x in ap._actions if isinstance(x, argparse._SubParsersAction)).choices
    if a.command not in subs:
        print(f"ошибка: нет команды {a.command}; команды: {', '.join(subs)}", file=sys.stderr)
        return 2
    subs[a.command].print_help()
    return 0


def _normalize(a, ap):
    """Пути проектов EDT — к их src; сочетания ключей, которые argparse не выражает."""
    if getattr(a, "source", None):      # project.source — каталог проекта EDT: берём его src
        try:
            root = _project(a)
        except (gitdiff.GitError, OSError):
            root = Path(a.repo)
        a.source = config.source_rel(root, a.source)
    for opt in ("dir", "base_dir", "parent"):      # указан каталог проекта EDT — берём его src
        if getattr(a, opt, None):
            setattr(a, opt, str(layout.source_root(getattr(a, opt))))
    if a.cmd in ("collect", "gate"):
        if a.base_dir and not a.dir:
            ap.error(f"{a.cmd}: --base-dir — только вместе с --dir")
        if a.dir and (a.worktree or a.source or a.base):
            ap.error(f"{a.cmd}: --dir — без git: не сочетается с --base, --worktree, --source")
        if a.cmd == "collect" and not (a.base or a.dir):
            ap.error("collect: нужен --base или --dir")
    if a.cmd == "ci" and a.action == "gate" and not a.base and not ci_mod.pr_target():
        ap.error("ci gate: нужен --base (не запрос на слияние: целевой ветки в переменных CI нет)")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):           # stderr — журнал gate --json
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = build_parser()
    a = ap.parse_args(argv)
    _normalize(a, ap)
    try:
        return a.func(a)
    except USER_ERRORS as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - код 1 — «ошибки в проверяемом коде», сбой самой ОТК — это 2
        traceback.print_exc()
        print(_internal(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
