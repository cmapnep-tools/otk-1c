"""Сценарий проверки: collect -> static -> build/run -> сводка (gate_run) и шаг статики."""
import dataclasses
import fnmatch
import json
import re
import time
from pathlib import Path
from typing import Any

from .. import analyzers, checks, config, model
from ..errors import InfraError
from ..probes import build as build_mod, designer_check, edt
from ..probes.platform import Platform, find_platform, platform_version
from ..sources import gitdiff, index as index_mod, layout, module_kinds as source_mod, revision, workspace
from ..sources.layout import Layout
from . import (
    collect as collect_mod,
    output,
    platform_api,
    report,
    resolvers,
    runner,
    scenarios as scen_mod,
    static,
    vendor as vendor_mod,
)


def api_checker(cfg: dict, configuration_xml: str | None, lay: Layout = layout.DESIGNER,
                names: dict[str, set[str]] | None = None) -> tuple[platform_api.ApiChecker | None, dict[str, Any]]:
    """Методы и типы платформы по справке платформы проверки (apicheck): (checker | None, сведения);
    names — словарь имён платформы (platform_names), по умолчанию встроенный."""
    env = cfg["environment"]
    configured = env.get("platform")
    bin_dir = Path(configured) if configured else find_platform()
    try:
        help_ix, others, notes = platform_api.open_help(platform_version(bin_dir) if bin_dir else None, bin_dir, env)
    except Exception as e:  # noqa: BLE001 — нет справки: шаг пропускается, остальная статика работает
        return None, {"skipped": f"справка платформы недоступна: {e}", "lack": f"справка платформы ({e})"}
    compat = platform_api.compat_mode(configuration_xml, lay)
    info = {"platform": help_ix.version, "compat": compat}
    min_platform = env.get("minPlatform")
    if min_platform and not re.fullmatch(r"\d+\.\d+\.\d+(\.\d+)?", str(min_platform)):
        notes = list(notes or []) + [f"environment.minPlatform = {min_platform!r}: нужна версия вида 8.3.24 — "
                                     "не учитывается"]
        min_platform = None
    if notes:
        info["notes"] = notes  # подмена версии, устаревший индекс — в примечания сводки
    return platform_api.ApiChecker(help_ix, compat, others, web_client=bool(env.get("webClient")),
                               min_platform=min_platform,
                               compat_warnings=env.get("compatWarnings", True) is not False,
                               names=names), info


def platform_names(cfg: dict) -> tuple[dict[str, set[str]], str | None]:
    """Словарь имён платформы: встроенный + [platformNames] настроек; ошибка в разделе — встроенный и примечание."""
    try:
        return platform_api.platform_names(cfg.get("platformNames")), None
    except ValueError as e:
        return platform_api.platform_names(), f"{e} — раздел не учитывается"


# находки статики, которые у расширения без выгрузки основной конфигурации могут быть ложными
UNRESOLVED_WITHOUT_PARENT = ("metadata-ref", "common-method", "form-name")


def static_step(repo: Path, cfg: dict, run_dir: Path, plan_path: Path, parent_arg: str | None = None,
                gate: checks.Gate | None = None) -> dict[str, Any]:
    """Статика по плану: static.json (schema/static.schema.json) рядом с планом; план не меняется — зависимые
    модули в `dependents`, решения реестра проверок шага — в `notChecked`/`infra`. Возвращает результат.
    gate (checks.Gate) — решения по проверкам bslls и platform-api; без него — по [checks] настроек."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    src = plan["source"]
    lay = layout.get(plan.get("sourceFormat"))
    parent_path = config.parent_dir(repo, cfg, parent_arg)
    parent = str(parent_path) if parent_path else None
    t0 = time.time()
    head_sha = plan.get("headSha")
    if plan.get("headDir") or (head_sha and head_sha != gitdiff.rev(repo, "HEAD")):
        # выгрузка без git (gate --dir) или редакция плана, а не рабочее дерево
        tree_root = Path(plan["headDir"]) if plan.get("headDir") else workspace.materialize(repo, head_sha, src,
                                                                                            run_dir / "trees")
        # без git — кеш по отпечатку каталога; редакция git (trees/<sha>) не меняется — достаточно маркеров
        ix = index_mod.cached_dir(tree_root / src, run_dir / "cache", full=bool(plan.get("headDir")))
        if parent:
            ix = index_mod.merge(ix, index_mod.cached_dir(Path(parent), run_dir / "cache"))
    else:
        tree_root = repo
        ix = index_mod.load(repo, src, run_dir / "cache", parent)
    t1 = time.time()

    def read_head(rel: str) -> str | None:
        p = tree_root / src / rel
        return layout.read_text(p)

    gate = gate or checks.Gate(cfg)
    names, names_note = platform_names(cfg)
    if gate.enabled("platform-api"):
        api, api_info = api_checker(cfg, read_head(lay.config_file), lay, names)
        if api_info.get("lack"):
            gate.lack("platform-api", api_info["lack"])
    else:
        api, api_info = None, {"off": True}
    if names_note:
        api_info["notes"] = list(api_info.get("notes", [])) + [names_note]

    def read_parent(rel: str) -> str | None:
        p = Path(parent) / rel if parent else None
        return layout.read_text(p) if p else None

    metadata = gate.enabled("metadata")
    findings = static.run(repo, plan, ix, read_head, api, read_parent, lay, metadata=metadata, names=names)
    if api and api.hidden_note():
        api_info["notes"] = list(api_info.get("notes", [])) + [api.hidden_note()]
    deps, dep_findings = static.dependents(tree_root / src, plan, ix) if metadata else ({}, [])
    findings.extend(dep_findings)
    if cfg["project"].get("extension") and not parent:
        for f in findings:
            if f["severity"] == "error" and f["check"] in UNRESOLVED_WITHOUT_PARENT:
                f["severity"] = "warning"
                f["message"] += " (нет --parent: возможно, объект основной конфигурации)"
    t2 = time.time()
    an_findings, an_info = analyzers.run(cfg, repo, run_dir, plan, tree_root, parent, lay, read_head, ix, gate)
    findings.extend(an_findings)
    t3 = time.time()
    known = {m["path"] for m in plan["modules"]}
    dependents = []
    for rel, lines in sorted(deps.items()):
        if rel in known:
            continue
        info = source_mod.describe(rel, read_head, plan["scriptVariant"], cfg["environment"]["ordinaryApplication"],
                                   lay, read_parent if parent else None, layout.detect(parent) if parent else None)
        dependents.append({
            "path": rel, "status": "M", "kind": info.kind, "owner": info.owner, "reason": "dependent",
            "contexts": info.contexts, "preprocessorConditions": [], "preprocessorContexts": [],
            "needsPlainServerProbe": False, "changedMethods": [], "removedMethods": [],
            "probe": info.probe, "uncovered": collect_mod.uncovered_of(info, plan["contextsAvailable"]),
            "dependentLines": lines})
    result = {**model.header("static", internal=True),
              "index": round(t1 - t0, 1), "checks": round(t2 - t1, 1), "analyzers": round(t3 - t2, 1),
              "analyzerInfo": an_info, "apiInfo": api_info, "findings": findings,
              "dependents": dependents,
              **gate.take(), "plan": model.plan_id(plan)}
    out = plan_path.with_name("static.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return result


def smoke_forms(forms: list[dict], smoke_cfg: dict) -> list[dict]:
    """Формы дымового теста открытия: изменённые формы без исключённых ([smoke] exclude — шаблоны полных имён,
    например «Обработка.*» или «*.ФормаНастроек»); выключить проверку — [checks] smoke = "off"."""
    patterns = [p.lower() for p in smoke_cfg.get("exclude", [])]
    return [f for f in forms if not any(fnmatch.fnmatchcase(f["form"].lower(), p) for p in patterns)]


def changes_outside(repo: Path, plan: dict, src: str) -> str | None:
    """В каталоге выгрузки src изменений нет, а файлы выгрузки (модули, Configuration.xml) изменены вне него —
    подсказка про --source; иначе None."""
    prefix = gitdiff.source_prefix(src)
    if not prefix:
        return None
    head = [plan["headSha"]] if plan.get("headSha") else []
    names = gitdiff.git(repo, "diff", "--name-only", "-z", plan["baseSha"], *head).split("\0")
    found = [n for n in names if n and not n.startswith(prefix)
             and (n.endswith(".bsl") or n.rsplit("/", 1)[-1] in ("Configuration.xml", "Configuration.mdo"))]
    if not found:
        return None
    return (f"в {src} изменений нет, а вне него изменено файлов выгрузки: {len(found)} (например {found[0]}) — "
            "выгрузка в другом каталоге? Укажите --source <каталог> или project.source в otk.toml")


def plan_notes(cfg: dict, parent: str | None = None, gate: checks.Gate | None = None) -> list[str]:
    """Примечания сценария до шагов (`notes` плана): неизвестные ключи настроек, [checks] и расширение без выгрузки
    основной конфигурации. Пишут gate и otk collect — шаги, запущенные по отдельности, дают те же примечания."""
    project = cfg["project"]
    return (config.warnings(cfg) + (gate or checks.Gate(cfg)).notes()
            + ([config.NO_PARENT] if project.get("extension") and not (parent or project.get("parent")) else []))


def designer_step(repo: Path, cfg: dict, run_dir: Path, plan: dict, built: bool, log: model.Log = print
                  ) -> dict[str, Any]:
    """Шаг designer (gate --designer): отрицательный контроль и проверка всех модулей Конфигуратором в той же
    копии ИБ, сверка с базовой линией designer-check. built — шаг run уже загрузил в копию редакцию плана; иначе
    (в плане только метаданные) — загрузка здесь. designer.json: {plan, ran, modes, control, rc, findings (новые
    относительно базовой линии), known, fixed, baseline, timings, notes, infra}; ran — проверка выполнена целиком
    (снимает «не покрыто» designer:*); схема — schema/designer.schema.json."""
    env, src = cfg["environment"], plan["source"]
    out: dict[str, Any] = {"ran": False, "modes": [], "findings": [], "notes": [], "infra": [], "timings": {}}
    ext = cfg["project"].get("extension")
    try:
        if env.get("connection"):
            raise InfraError("--designer — только в своей копии ИБ (environment.ib), не в рабочей базе",
                             code="config-invalid")
        if not env.get("ib"):
            raise InfraError("--designer: нет копии ИБ (environment.ib)", code="no-ib")
        tree = workspace.tree_root(repo, plan, run_dir)
        dump = Path(tree) / src
        platform = Platform(env, Path(run_dir))
        # режимы — до сборки: ошибка настройки не должна стоить загрузки в копию
        out["modes"] = designer_check.modes_of(cfg, config.parent_dir(repo, cfg) or dump)
        if not built:
            t = time.time()
            try:
                if plan.get("headDir"):
                    build_mod.build_dirs(plan, platform, run_dir, extension=ext)
                else:
                    build_mod.build(repo, plan["headSha"] or gitdiff.rev(repo, "HEAD"), src, tree, platform, run_dir,
                                    extension=ext)
            except build_mod.LoadError as e:
                out["findings"] = [{"object": rel, "file": f"{src}/{rel}", "line": 1, "col": 1, "message": message,
                                    "code": "", "modes": [], "load": True} for rel, message in e.errors]
                out["notes"].append("проверка Конфигуратором не выполнялась: выгрузка не загружается в копию ИБ")
                return _designer_write(run_dir, out, plan)
            out["timings"]["build"] = round(time.time() - t, 1)
        baseline_path = designer_check.baseline_of(repo, cfg)
        res = designer_check.check(platform, dump, src, out["modes"], baseline_path, run_dir,
                                   ["-Extension", ext] if ext else [], log=log)
        out["timings"].update(res.pop("timings"))
        out["notes"] += res.pop("notes")
        code = res.pop("code")
        out.update(res)
        if code == 2:
            # контроль не сработал или rc без разобранных ошибок — не проверено, а не зелёный прогон
            out["infra"].append({"message": out["notes"].pop(), "reasonCode": "platform-failed"})
        else:
            out["ran"] = True
    except InfraError as e:
        out["infra"].append({"message": f"проверка Конфигуратором: {e}", "reasonCode": e.code})
    return _designer_write(run_dir, out, plan)


def _designer_write(run_dir: Path, out: dict[str, Any], plan: dict) -> dict[str, Any]:
    out["plan"] = model.plan_id(plan)
    data = {**model.header("designer", internal=True), **out}
    (Path(run_dir) / "designer.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
    return out


def resolve_queries(plan: dict, repo: Path, cfg: dict, run_dir: Path, gate: checks.Gate | None = None,
                    static_only: bool = False, log: model.Log = print) -> list[str]:
    """Внешние восстановители собранных запросов — часть collect: план после них не меняется.
    Возвращает примечания сводки."""
    if not resolvers.configured(cfg) or not (gate or checks.Gate(cfg)).enabled("queries"):
        return []
    if static_only:
        return ["внешние восстановители запросов не вызывались: --static-only"]
    return resolvers.resolve(plan, repo, revision.read_head(repo, plan), Path(run_dir) / "cache" / "resolvers",
                             cfg, log)


@dataclasses.dataclass(frozen=True)
class GateOptions:
    """Что проверяет gate_run. Git: base — базовая ревизия (None — все модули выгрузки, база — пустой коммит,
    gate --all), head или worktree — проверяемая редакция, source — каталог выгрузки. Без git: head_dir — каталог
    выгрузки, base_dir — выгрузка «было» или None — все модули. Контракт без git: копия ИБ уже содержит base_dir —
    загружаются только отличия head_dir (без base_dir — полная загрузка). vendor — доработки ИБ относительно её
    конфигурации поставщика (vendor.prepare): «стало» — выгрузка ИБ, загрузки в ИБ нет. designer — плюс проверка
    всех модулей Конфигуратором в той же копии ИБ (designer_step), одна сводка и один код выхода."""
    base: str | None = None
    head: str = "HEAD"
    worktree: bool = False
    source: str | None = None
    head_dir: str | None = None
    base_dir: str | None = None
    parent: str | None = None
    vendor: bool = False
    static_only: bool = False
    fast: bool = False
    all_scenarios: bool = False
    designer: bool = False


def gate_run(repo: Path, cfg: dict, run_dir: Path, opts: GateOptions, log: model.Log = print) -> dict[str, Any]:
    """Вся проверка: collect -> static -> build/run -> [designer] -> сводка. Возвращает сводку (code — код выхода)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    vendor_prep = vendor_mod.prepare(cfg, run_dir, log=log) if opts.vendor else None
    if vendor_prep:
        opts = dataclasses.replace(opts, head_dir=str(vendor_prep["head"]), base_dir=str(vendor_prep["base"]))
    gate = checks.Gate(cfg)
    timings: dict[str, float] = {}
    plan, plan_path = _plan_step(repo, cfg, run_dir, opts, gate, vendor_prep, timings, log)
    built, skip_designer = False, None       # шаг run загрузил редакцию в копию ИБ; почему designer не выполнять
    if plan["modules"] or plan["queries"] or plan["scenarios"] or plan["smoke"]:
        built, skip_designer = _probe_step(repo, cfg, run_dir, opts, plan, plan_path, gate, timings, log)
    if opts.designer:
        t = time.time()
        if skip_designer:
            _designer_write(run_dir, {"ran": False, "modes": [], "findings": [], "notes": [skip_designer],
                                      "infra": [], "timings": {}}, plan)
        else:
            d = designer_step(repo, cfg, run_dir, plan, built, log=lambda m: log(f"  {m}"))
            log(f"Конфигуратор: новых ошибок {len(d['findings'])}" + (f", сбой: {d['infra'][0]}" if d["infra"] else ""))
        timings["designer"] = round(time.time() - t, 1)
    if vendor_prep:
        vendor_mod.cleanup(run_dir)
    summary = report.from_files(run_dir, timings, lambda: workspace.tree_root(repo, plan, run_dir))
    output.write(summary, run_dir, Path(opts.head_dir) if opts.head_dir else repo)
    return summary


def _plan_step(repo: Path, cfg: dict, run_dir: Path, opts: GateOptions, gate: checks.Gate,
               vendor_prep: dict | None, timings: dict[str, float], log: model.Log) -> tuple[dict, Path]:
    """Шаг collect: план с запросами, сценариями и формами дымового теста -> otk.json. Возвращает (план, путь)."""
    if opts.base is None and opts.head_dir is None:
        opts = dataclasses.replace(opts, base=gitdiff.empty_commit(repo))
    env = cfg["environment"]
    src = "." if opts.head_dir else opts.source or cfg["project"]["source"]
    # нет выгрузки основной конфигурации — сразу и понятно, а не в индексе
    parent_root = config.parent_dir(repo, cfg, opts.parent)
    notes = plan_notes(cfg, opts.parent, gate)
    project = cfg["project"]
    if project.get("extensionDetected"):
        log(f"расширение «{project['extension']}» — распознано по файлу конфигурации")
    if project.get("sourceDetected") and not opts.source and not opts.head_dir:
        log(f"выгрузка — {src} (в корне репозитория; project.source не задан)")
    head = opts.head
    if opts.worktree:
        stash = gitdiff.git(repo, "stash", "create").strip()   # коммит с рабочим деревом; ветку и индекс не трогает
        head = stash or "HEAD"
        untracked = gitdiff.git(repo, "ls-files", "--others", "--exclude-standard", "--", src).split()
        if untracked:
            notes.append(f"неотслеживаемые файлы не проверяются (git add): {', '.join(untracked[:5])}"
                         + (" …" if len(untracked) > 5 else ""))
    t = time.time()
    if opts.head_dir:
        plan = collect_mod.collect_dirs(opts.base_dir, opts.head_dir, env["contexts"], env["ordinaryApplication"],
                                        parent_root)
    else:
        plan = collect_mod.collect(repo, opts.base, head, src, env["contexts"], env["ordinaryApplication"],
                                   parent_root)
        outside = None if plan["modules"] or plan["metadata"] else changes_outside(repo, plan, src)
        if outside:
            notes.append(outside)
            log(outside)
    if vendor_prep:
        plan["vendor"] = vendor_prep["info"]
        notes += vendor_prep["notes"]
    notes += resolve_queries(plan, repo, cfg, run_dir, gate, opts.static_only, log)
    plan_path = run_dir / "otk.json"
    sc_cfg = cfg.get("scenarios", {})
    exts = scen_mod.discover(repo, sc_cfg.get("path", scen_mod.DEFAULT_PATH))
    plan["scenarioExtensions"] = [{"name": e["name"], "dir": e["dir"]} for e in exts]
    plan["scenarios"] = scen_mod.select(plan, exts, opts.all_scenarios)
    plan["allScenarios"] = opts.all_scenarios
    if exts:
        log(f"сценарии: тестов {sum(len(e['tests']) for e in exts)}, выбрано {len(plan['scenarios'])}")
    plan["smoke"] = smoke_forms(plan["forms"], cfg.get("smoke", {})) if gate.enabled("smoke") else []
    plan["notes"] = notes
    plan_path.write_text(collect_mod.dumps(plan), encoding="utf-8")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))   # отпечаток (model.plan_id) — по записанному плану
    for stale in ("static.json", "run.json", "designer.json"):   # файлы шагов прошлого прогона не нужны и в каталоге
        (run_dir / stale).unlink(missing_ok=True)
    timings["collect"] = round(time.time() - t, 1)
    log(f"план: модулей {len(plan['modules'])}, запросов {len(plan['queries'])} ({timings['collect']} c)")
    return plan, plan_path


def _probe_step(repo: Path, cfg: dict, run_dir: Path, opts: GateOptions, plan: dict, plan_path: Path,
                gate: checks.Gate, timings: dict[str, float], log: model.Log) -> tuple[bool, str | None]:
    """Статика и пробы в копии ИБ; пробы не выполняются — решения и примечания всё равно пишутся в run.json.
    Возвращает (редакция загружена в копию ИБ, почему проверку Конфигуратором не выполнять или None)."""
    env = cfg["environment"]
    t = time.time()
    static_res = static_step(repo, cfg, run_dir, plan_path, opts.parent, gate)
    timings["static"] = round(time.time() - t, 1)
    static_fail = any(f["severity"] == "error" for f in static_res["findings"])
    log(f"статика: ошибок {sum(1 for f in static_res['findings'] if f['severity'] == 'error')} "
        f"({timings['static']} c)")

    def not_run(lack: str | None = None) -> None:
        """Пробы не выполнялись: решение по каждой — «нет ИБ» или lack (недостающая зависимость)."""
        full = model.with_dependents(plan, static_res)
        work = {"probes-server": full["modules"], "probes-client": full["modules"], "extcheck": full["modules"],
                "queries": plan["queries"], "scenarios": plan["scenarios"], "smoke": plan["smoke"]}
        for name, items in work.items():
            if items and lack:
                gate.lack(name, lack)
            elif items:
                gate.ready(name, {"ib": False})

    if opts.static_only:
        runner.write(run_dir, {"notes": ["пробы в базе не выполнялись: --static-only"]}, gate, plan)
        return False, None
    if not (env.get("ib") or env.get("connection")):
        not_run()
        runner.write(run_dir, {}, gate, plan)
        return False, None
    if opts.fast and static_fail:
        runner.write(run_dir, {"notes": ["пробы в базе пропущены (--fast): статика уже нашла ошибки"]}, gate, plan)
        return False, "проверка Конфигуратором пропущена (--fast): статика уже нашла ошибки"
    built, skip_designer = False, None
    t = time.time()
    try:
        run_out = runner.run(repo, plan, cfg, run_dir, workspace.tree_root(repo, plan, run_dir),
                             log=lambda m: log(f"  {m}"), gate=gate, static=static_res)
        timings.update({f"run.{k}": v for k, v in run_out["timings"].items()})
        if any(r["kind"] == "load" for r in run_out["results"]):
            skip_designer = "проверка Конфигуратором не выполнялась: выгрузка не загружается в копию ИБ"
        built = not env.get("connection")
    except edt.NoEdtcli:
        # исходники EDT, нужен экспорт, а 1cedtcli нет (правки одних модулей грузятся и без него)
        not_run(checks.DEPS["edtcli"])
        runner.write(run_dir, {"notes": runner.base_notes(plan)}, gate, plan)
        skip_designer = "проверка Конфигуратором не выполнялась: нет 1cedtcli для экспорта EDT"
    except InfraError as e:
        runner.write(run_dir, {"notes": runner.base_notes(plan),
                               "infra": [{"message": str(e), "reasonCode": e.code}]}, gate, plan)
        skip_designer = "проверка Конфигуратором не выполнялась: сбой шага run"
    timings["run"] = round(time.time() - t, 1)
    return built, skip_designer
