"""Сбор изменений: git diff или сравнение каталогов -> план проверки otk.json (спецификация
change-detection)."""
import bisect
import json
from pathlib import Path

from .. import lang, model
from ..errors import InfraError
from ..sources import bsl, dirdiff, gitdiff, layout, module_kinds, queries


def _method_dict(m):
    return {"name": m.name, "kind": m.kind, "export": m.export,
            "params": [p.name for p in m.params], "lines": [m.start, m.end],
            **({"directive": m.directive} if m.directive else {}),
            **({"annotations": m.annotations} if m.annotations else {})}


# средство, которое закроет модуль без пробы: модуль команды — UI/сценарные тесты, сервис — публикация;
# остальное — полная проверка Конфигуратором
NEEDS_BY_KIND = {"CommandModule": "ui", "HTTPService": "publication", "WebService": "publication"}


def uncovered_of(info, available):
    """«Не покрыто этим прогоном» модуля: контексты, недоступные локально, и модуль без пробы — для
    изменённых и зависимых модулей. needs — чем закрыть: designer:<контекст> (полная проверка Конфигуратором
    в режиме; * — во всех), ui, publication."""
    out = [{"context": ctx, "needs": f"designer:{ctx}", "reason": "контекст недоступен локально"}
           for ctx in info.contexts if ctx not in available]
    if info.probe["strategy"] == "none":
        out.append({"context": "*", "needs": NEEDS_BY_KIND.get(info.kind, "designer:*"),
                    "reason": info.probe["reason"]})
    return out


def _module_entry(info, change, base_text, head_text, available, lexicon_body):
    head = bsl.parse(head_text) if head_text is not None else None
    base = bsl.parse(base_text) if base_text is not None else None
    touched = change.added_lines | change.deleted_at
    if change.status == "A" or base is None:
        touched = set(range(1, head_text.count("\n") + 2))

    lines = sorted(touched)
    base_names = base.by_name() if base else {}
    changed_methods, body_changed = [], False
    for m in head.methods:
        i = bisect.bisect_left(lines, m.start)
        if i < len(lines) and lines[i] <= m.end:
            entry = _method_dict(m)
            old = base_names.get(m.name.lower())
            entry["new"] = old is None
            entry["signatureChanged"] = bool(old) and old.signature() != m.signature()
            changed_methods.append(entry)
    if not head.code_lines.isdisjoint(touched):
        body_changed = True
    if body_changed:
        changed_methods.append({"name": lexicon_body, "kind": "body"})

    removed = []
    if base:
        head_names = head.by_name()
        removed = [_method_dict(m) for m in base.methods if m.name.lower() not in head_names]

    conditions = set()
    for cond in {head.conditions[ln] for ln in lines if ln < len(head.conditions)}:
        conditions.update(cond)
    symbols, negated_ec = bsl.condition_symbols(conditions)

    uncovered = uncovered_of(info, available)

    changed_lines = set()
    changed_names = {x["name"] for x in changed_methods}
    for m in head.methods:
        if m.name in changed_names:
            changed_lines.update(range(m.start, m.end + 1))
    if body_changed:
        changed_lines.update(head.code_lines)
    found = queries.extract(head, changed_lines)
    if base and found:
        # тот же текст есть в базовой версии модуля — запрос правкой не менялся (его ошибка была и до неё)
        before = {" ".join(q["text"].split()) for q in queries.extract(base) if q["complete"]}
        for q in found:
            q["unchanged"] = q["complete"] and " ".join(q["text"].split()) in before

    entry = {
        "path": info.path, "status": change.status, "kind": info.kind, "owner": info.owner,
        "reason": "changed", "contexts": info.contexts,
        "preprocessorConditions": sorted(conditions),
        "preprocessorContexts": sorted(symbols),
        "needsPlainServerProbe": negated_ec,
        "changedMethods": changed_methods,
        "removedMethods": removed,
        "probe": info.probe, "uncovered": uncovered,
    }
    if change.old_path:
        entry["renamedFrom"] = change.old_path
    if info.flags:
        entry["flags"] = info.flags
    if info.companions:
        entry["loadWith"] = info.companions
    return entry, found


def collect(repo, base, head, source_dir, contexts_available, ordinary_application=False, parent=None,
            required_contexts=()):
    """План по git: base..head (head = gitdiff.WORKTREE — рабочее дерево). parent — каталог основной конфигурации
    (флаги заимствованных общих модулей расширения); required_contexts — контексты, покрытие которых обязательно."""
    repo = Path(repo)
    prefix = gitdiff.source_prefix(source_dir)
    base_sha = gitdiff.rev(repo, base)
    head_sha = gitdiff.rev(repo, head) if head is not gitdiff.WORKTREE else None
    with gitdiff.Blobs(repo) as blobs:
        changes = sorted(gitdiff.changes(repo, base_sha, head, prefix), key=lambda c: c.path)
        everything = gitdiff.is_empty_tree(repo, base_sha)     # gate --all: diff не нужен, все строки новые
        if not everything:
            modules = [c for c in changes if c.status != "D" and module_kinds.is_module(c.path[len(prefix):])]
            gitdiff.fill_lines_batch(repo, base_sha, head, modules,
                                     prefix)
        for ch in changes:                                     # дальше пути — от корня выгрузки
            ch.path = ch.path[len(prefix):]
            ch.old_path = ch.old_path[len(prefix):] if ch.old_path else None
        header = {"base": base, "baseSha": base_sha,
                  "head": head if head is not gitdiff.WORKTREE else "WORKTREE", "headSha": head_sha}
        return _collect(header, source_dir, changes, everything, lambda rel: blobs.show(base_sha, prefix + rel),
                        lambda rel: blobs.show(head, prefix + rel), contexts_available, ordinary_application, parent,
                        required_contexts)


def collect_dirs(base_dir, head_dir, contexts_available, ordinary_application=False, parent=None,
                 required_contexts=()):
    """План без git: head_dir — каталог выгрузки (файл конфигурации в корне: Configuration.xml или
    Configuration/Configuration.mdo); base_dir — выгрузка «было» или None (все модули, как gate --all)."""
    head_dir = layout.source_root(Path(head_dir).resolve())
    base_dir = layout.source_root(Path(base_dir).resolve()) if base_dir else None
    for d in (head_dir, base_dir):
        if d and layout.detect(d) is None:
            raise InfraError(f"{d}: не выгрузка конфигурации (нет файла конфигурации в корне)", code="no-source")
    changes = dirdiff.changes(base_dir, head_dir, module_kinds.is_module)
    base_str = str(base_dir) if base_dir else None
    header = {"base": base_str, "baseSha": None, "baseDir": base_str,
              "head": str(head_dir), "headSha": None, "headDir": str(head_dir)}
    return _collect(header, ".", changes, base_dir is None,
                    (lambda rel: dirdiff.read(base_dir, rel)) if base_dir else (lambda rel: None),
                    lambda rel: dirdiff.read(head_dir, rel), contexts_available, ordinary_application, parent,
                    required_contexts)


def _collect(header, source_dir, changes, everything, read_base, read_head, contexts_available, ordinary_application,
             parent=None, required_contexts=()):
    """changes — изменения с путями от корня выгрузки (строки заполнены, кроме everything — все строки новые);
    read_base / read_head(rel) -> текст файла в редакции «было» / «стало» или None."""
    # формат — по «стало», затем по «было» (проект мог перейти из одного формата в другой)
    lay = layout.detect_read(read_head) or layout.detect_read(read_base)
    if lay is None and not changes:
        lay = layout.DESIGNER        # изменений выгрузки нет (push без правок 1С) — формат не важен
    if lay is None:
        raise InfraError("нет файла конфигурации (Configuration.xml или Configuration/Configuration.mdo) "
                         f"в {source_dir}", code="no-source")
    parent_lay = layout.detect(parent) if parent else None      # основная конфигурация — со своим форматом
    read_parent = (lambda rel: dirdiff.read(parent, rel)) if parent_lay else None
    parent_lay = parent_lay or lay
    cfg_text = read_head(lay.config_file) or read_base(lay.config_file)
    props = lay.properties(cfg_text)
    variant = props.variant
    words = lang.dictionary(variant)       # UnsupportedScriptVariant -> код 2 в CLI
    # обычное приложение — по настройке проекта или основному режиму запуска конфигурации
    ordinary_application = ordinary_application or props.run_mode == "OrdinaryApplication"
    if "OrdinaryClient" in required_contexts and not ordinary_application:
        # без этого контекста не будет ни у одного модуля плана, и требование молча считалось бы выполненным
        raise InfraError("requiredContexts: OrdinaryClient, но конфигурация не работает в обычном приложении "
                         f"(DefaultRunMode = {props.run_mode or 'не задан'}, environment.ordinaryApplication = false) "
                         "— проверить этот контекст нечем", code="config-invalid")

    modules, removed_modules, metadata, all_queries = [], [], [], []
    for ch in changes:
        rel = ch.path
        if not module_kinds.is_module(rel):
            metadata.append({"path": rel, "status": ch.status,
                             **({"renamedFrom": ch.old_path} if ch.old_path else {})})
            continue
        if lay.has_protected and lay.protected(read_base(ch.old_path or rel) if ch.status == "D" else read_head(rel)):
            metadata.append({"path": rel, "status": ch.status,      # защищённый модуль: исходного текста нет, как .bin
                             **({"renamedFrom": ch.old_path} if ch.old_path else {})})
            continue
        if ch.status == "D":
            info = module_kinds.describe(rel, read_base, variant, ordinary_application, lay, read_parent, parent_lay)
            base_mod = bsl.parse(read_base(rel) or "")
            removed_modules.append({"path": rel, "kind": info.kind, "owner": info.owner,
                                    "exports": [m.name for m in base_mod.methods if m.export]})
            continue
        info = module_kinds.describe(rel, read_head, variant, ordinary_application, lay, read_parent, parent_lay)
        base_text = read_base(ch.old_path or rel) if ch.status != "A" else None
        head_text = read_head(rel)
        if everything:
            ch.added_lines.update(range(1, len((head_text or "").splitlines()) + 1))
        entry, found = _module_entry(info, ch, base_text, head_text, contexts_available,
                                     words["words"]["bodyPseudoMethod"])
        if not entry["changedMethods"] and not entry["removedMethods"]:
            entry["note"] = "изменения только в комментариях или пустых строках"
        modules.append(entry)
        for q in found:
            all_queries.append({"module": rel, **q})

    for n, q in enumerate(all_queries, 1):
        q["id"] = f"q{n}"
    forms = changed_forms(modules, metadata, read_head, variant, ordinary_application, lay, read_parent, parent_lay)
    plan = {
        **model.header("plan"),
        **header,
        "sourceFormat": lay.format, "source": source_dir, "scriptVariant": variant,
        "contextsAvailable": list(contexts_available),
        "requiredContexts": list(required_contexts),
        "modules": modules,
        "removedModules": removed_modules,
        "metadata": metadata,
        "queries": [{k: q[k] for k in ("id", "module", "line", "complete", "assembled", "unchanged", "reason", "text")
                     if k in ("id", "module", "line", "complete", "text") or q.get(k)} for q in all_queries],
        "scenarios": [],
        "forms": forms,
    }
    return plan


def changed_forms(modules, metadata, read_head, variant="Russian", ordinary_application=False, lay=layout.DESIGNER,
                  read_parent=None, parent_lay=None):
    """Изменённые управляемые формы — кандидаты дымового теста открытия: изменён модуль формы или её
    Form.xml (элементы, динамические списки). [{form, path, reason: module|layout}], path — модуль формы,
    если он есть, иначе Form.xml. Обычные (неуправляемые) формы — по FormType описания формы — пропускаются:
    в тонком клиенте они не открываются."""
    def ordinary(module):
        a = lay.parse(module)   # модули форм плана — из describe, parse их уже распознал
        desc = read_head(lay.form_files(a.collection, a.name, a.sub)[0]) or "" if a else ""
        return lay.ordinary_form(desc, a.sub)

    out, seen = [], set()
    for m in modules:
        if m["kind"] == "FormModule" and m["probe"]["strategy"] == "get-form" and not ordinary(m["path"]):
            out.append({"form": m["owner"], "path": m["path"], "reason": "module"})
            seen.add(m["owner"])
    for md in metadata:
        module = lay.form_module(md["path"])
        if md["status"] == "D" or module is None:
            continue
        info = module_kinds.describe(module, read_head, variant, ordinary_application, lay, read_parent, parent_lay)
        if info.kind != "FormModule" or info.owner in seen or ordinary(module):
            continue
        seen.add(info.owner)
        out.append({"form": info.owner, "path": module if read_head(module) is not None else md["path"],
                    "reason": "layout"})
    return out


def dumps(plan):
    return json.dumps(plan, ensure_ascii=False, indent=1) + "\n"
