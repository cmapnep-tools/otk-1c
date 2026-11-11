"""Сборка копии ИБ под редакцию плана (спецификация ib-build).

Состояние описывает копию ИБ, поэтому лежит рядом с ней — otk-state.json в каталоге файловой ИБ (каталог прогона
у разных репозиториев и рабочих деревьев свой, а копия ИБ может быть общей): ИБ, коммит и хеш дерева выгрузки
последней успешной загрузки, инструменты; время создания 1Cv8.1CD — пересозданная ИБ не считается собранной. Без
каталога ИБ (рабочая база по connection) — build-state.json в каталоге прогона, как раньше; оттуда же состояние
подхватывается при первом запуске. Дерево не изменилось — пропуск; изменились только существующие файлы —
частичная загрузка (форма — описанием формы, не файлом из её `Ext`) + UpdateDBCfg; удаления, переименования,
Configuration.xml или нет состояния — полная загрузка.

Исходники EDT грузятся экспортом 1cedtcli; частичная загрузка одних модулей основной конфигурации — без экспорта
(edt.from_ib): модули из EDT, Form.xml к модулям форм — частичной выгрузкой копии ИБ (форма не менялась).

Без git (gate --dir) — контракт: копия ИБ уже содержит выгрузку «было» (--base-dir), ОТК это не проверяет;
загружаются отличия «стало» по плану (те же правила частичной/полной загрузки). Без --base-dir — полная загрузка.
После загрузки без git в состоянии нет коммита — следующая сборка по git будет полной.

Полная загрузка — `ibcmd infobase config import` при любом environment.ibTool: втрое быстрее пакетного
Конфигуратора (УМЦ, 12,9 тыс. файлов, в пустую ИБ: 56 с против 182 с, 8.3.27, 2026-10-26); без ibcmd (клиентская
установка платформы) — Конфигуратором (Platform.full_load).
Частичная загрузка и обновление БД — инструментом environment.ibTool (Platform.load_files, update_db):
Конфигуратором по умолчанию или ibcmd.
"""
import json
import re
from pathlib import Path

from ..errors import InfraError
from ..sources import gitdiff, layout, workspace
from . import edt

# «[ERROR] Файл - <путь>: <текст>» (ibcmd) и «Файл - <путь>: <текст>» (Конфигуратор): ошибка в исходниках
_FILE_ERROR = re.compile(r"^(?:\[ERROR\]\s*)?(?:Файл|File) - (.+?\.(?:xml|bsl|txt|html|json)): (.+?)\s*$", re.M | re.I)


class LoadError(Exception):
    """Выгрузка не загружается из-за ошибок в её файлах (неверный путь к данным формы и т.п.) — это находки
    проверки, а не сбой инфраструктуры. errors — [(путь относительно выгрузки, текст)]."""

    def __init__(self, errors):
        super().__init__("; ".join(f"{p}: {m}" for p, m in errors[:3]))
        self.errors = errors


EXPORT_MARK = "(файл выгрузки, экспортированной из EDT) "


def source_errors(out, dump, lay=None):
    """Ошибки файлов выгрузки из вывода загрузки: [(путь относительно выгрузки, текст)] без повторов.
    lay — формат исходников проекта, если dump получена экспортом из них (EDT): пути пересчитываются в исходники,
    не пересчитываемые остаются путями выгрузки, а пометка EXPORT_MARK — в начале текста ошибки."""
    root = str(Path(dump)).replace("\\", "/").rstrip("/").lower() + "/"
    seen, errors = set(), []
    for path, message in _FILE_ERROR.findall(out or ""):
        norm = path.replace("\\", "/")
        rel = norm[len(root):] if norm.lower().startswith(root) else norm
        if lay is not None and lay is not layout.DESIGNER:
            mapped = lay.from_designer(rel)
            rel, message = (mapped, message) if mapped else (rel, EXPORT_MARK + message)
        if (rel, message) not in seen:
            seen.add((rel, message))
            errors.append((rel, message))
    return errors


STATE_FILE = "otk-state.json"
RUN_STATE = "build-state.json"         # каталога файловой ИБ нет (серверная, ещё не создана) — в каталоге прогона


def _ib_dir(ib):
    p = Path(ib) if ib else None
    return p if p is not None and p.is_dir() else None


def _ib_created(ib):
    d = _ib_dir(ib)
    f = d / "1Cv8.1CD" if d else None
    return int(f.stat().st_ctime) if f and f.exists() else None


def state_path(run_dir, ib=None):
    d = _ib_dir(ib)
    return d / STATE_FILE if d else Path(run_dir) / RUN_STATE


def load_state(run_dir, ib=None):
    p = state_path(run_dir, ib)
    if not p.exists():
        return None
    state = json.loads(p.read_text(encoding="utf-8"))
    created = _ib_created(ib) if ib is not None else None
    if created is not None and state.get("ibCreated") not in (None, created):
        return None                                     # ИБ пересоздана — что в ней, неизвестно
    return state


def save_state(run_dir, state, ib=None):
    created = _ib_created(ib) if ib is not None else None
    if created is not None:
        state = {**state, "ibCreated": created}
    p = state_path(run_dir, ib)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def tree_hash(repo, sha, src):
    return gitdiff.git(repo, "rev-parse", f"{sha}:{gitdiff.source_prefix(src).rstrip('/')}").strip()


def form_description(rel, lay=layout.DESIGNER):
    """Описание формы, которой принадлежит изменённый файл формы (модуль или содержимое); иначе None.

    Конфигуратор путь внутри `Ext` принимает за объект метаданных: `CommonForms/Ф/Ext/Form.xml` — это для него
    «CommonForm.Ф.Ext», загрузка падает (rc≠0, 8.3.23). Описание формы (`CommonForms/Ф.xml`,
    `Catalogs/Т/Forms/Ф.xml`) платформа загружает вместе с содержимым формы и её модулем."""
    a = lay.parse(rel)
    if a is None:                          # файл содержимого формы: …/Ext/Form.xml
        module = lay.form_module(rel)
        a = lay.parse(module) if module else None
    if a is not None and a.kind == "FormModule":
        return lay.form_files(a.collection, a.name, a.sub)[0]
    return None


def _load_list(changes, lay=layout.DESIGNER):
    """[(статус, путь от корня выгрузки)] -> файлы частичной загрузки или None, если нужна полная.
    Исходники EDT грузятся экспортом в выгрузку Конфигуратора: список — в её путях."""
    if lay is not layout.DESIGNER:
        converted = [(status, lay.to_designer(rel)) for status, rel in changes]
        if any(rel is None for _, rel in converted):
            return None                     # не пересчитывается однозначно — полная загрузка
        changes, lay = converted, layout.DESIGNER
    files = set()
    for status, rel in changes:
        if status in ("D", "R") or rel in lay.dump_markers:
            return None
        if status == "A" and not rel.endswith(".bsl"):
            return None                     # новый объект метаданных — надёжнее полная загрузка
        desc = form_description(rel, lay)
        if desc is None:
            files.add(rel)
        else:                               # форма грузится описанием: модуль и содержимое — вместе с ним
            files.add(desc)
    return sorted(files)


def edt_modules(changes, lay, extension=None):
    """Изменения исходников EDT, загружаемые без экспорта: только изменённые и новые модули основной
    конфигурации — их пути EDT; иначе None (экспорт)."""
    if lay is not layout.EDT or extension or not changes:
        return None
    if any(status not in ("M", "A") or lay.parse(rel) is None for status, rel in changes):
        return None
    return sorted(rel for _, rel in changes)


def _git_changes(repo, src, from_sha, to_sha):
    prefix = gitdiff.source_prefix(src)
    return [(ch.status, ch.path[len(prefix):]) for ch in gitdiff.changes(repo, from_sha, to_sha, prefix)]


def plan_load(repo, src, from_sha, to_sha, lay=layout.DESIGNER):
    """Список файлов для частичной загрузки или None, если нужна полная. lay — формат исходников."""
    return _load_list(_git_changes(repo, src, from_sha, to_sha), lay)


def _dir_changes(plan):
    changes = [("R" if m.get("renamedFrom") else m["status"], m["path"]) for m in plan["modules"]]
    changes += [("D", m["path"]) for m in plan["removedModules"]]
    changes += [("R" if m.get("renamedFrom") else m["status"], m["path"]) for m in plan["metadata"]]
    return changes


def plan_load_dirs(plan):
    """Частичная загрузка без git — по изменениям плана (baseDir -> headDir); None — полная."""
    if not plan.get("baseDir"):
        return None
    return _load_list(_dir_changes(plan), layout.get(plan.get("sourceFormat")))


def plan_from_state(repo, src, source_root, state, head_sha, ib, full=False, extension=None):
    """Что загружать в копию ИБ по её состоянию — общий расчёт build и build --dry-run: (files, modules, lost).
    files — файлы частичной загрузки (None — полная), modules — модули EDT без экспорта,
    lost — коммита прошлой загрузки в репозитории уже нет (разницу не построить)."""
    if not (state and state.get("ib") == str(ib) and not full):
        return None, None, False
    commit = state.get("commit")
    if not commit:
        return None, None, False
    if not _commit_exists(repo, commit):
        return None, None, True
    src_lay = layout.detect(Path(source_root) / src) or layout.DESIGNER
    changes = _git_changes(repo, src, commit, head_sha)
    files = _load_list(changes, src_lay)
    return files, (edt_modules(changes, src_lay, extension) if files else None), False


def create_ib_plan(ib, connection=None, extension=None):
    """Проверить без записи, можно ли создать файловую ИБ; True — каталог пуст или отсутствует."""
    if connection or ib is None:
        return False
    ib = Path(ib)
    if (ib / "1Cv8.1CD").exists():
        return False
    if ib.exists() and (not ib.is_dir() or any(ib.iterdir())):
        raise InfraError(f"копия ИБ {ib}: каталог не пуст, а файловой ИБ (1Cv8.1CD) в нём нет — укажите копию ИБ "
                         "или пустой (несуществующий) каталог, ОТК создаст ИБ сама", code="ib-invalid")
    if extension:
        raise InfraError(f"копии ИБ {ib} нет: для проверки расширения нужна копия ИБ с основной конфигурацией",
                         code="ib-invalid")
    return True


def ensure_ib(platform, extension=None):
    """Своей копии ИБ (environment.ib, не connection) нет или её каталог пуст — новая пустая файловая ИБ
    (CREATEINFOBASE), загрузка после неё полная. Возвращает True, если ИБ создана."""
    ib = platform.ib
    if not create_ib_plan(ib, platform.connection, extension):
        return False
    rc, out = platform.create_file_ib(ib)
    if rc or not (ib / "1Cv8.1CD").exists():
        raise InfraError(f"не создана копия ИБ {ib}: rc={rc} {out[-300:]}", code="platform-failed")
    return True


def build(repo, head_sha, src, source_root, platform, run_dir, extension=None, full=False, assume_current=False):
    """source_root — каталог с выгрузкой редакции head_sha (рабочее дерево или извлечённая редакция).
    Возвращает словарь с описанием выполненного шага."""
    tree = tree_hash(repo, head_sha, src)
    platform.lock()                       # до чтения состояния: другой прогон мог бы сменить его после
    created = not assume_current and ensure_ib(platform, extension)
    # у новой ИБ состояния нет: прежнее из каталога прогона (build-state.json) догрузило бы разницу в пустую базу
    state = None if created else load_state(run_dir, platform.ib)
    new_state = {"ib": str(platform.ib), "commit": head_sha, "tree": tree, "dir": None, "extension": extension}
    if assume_current:
        save_state(run_dir, {**(state or {}), **new_state}, platform.ib)
        return {"action": "marked", "commit": head_sha}
    if state and state.get("ib") == str(platform.ib) and state.get("tree") == tree and not full:
        return {"action": "up-to-date", "commit": state["commit"]}

    # коммит прошлой загрузки мог исчезнуть: пакеты controls и пустая база --all — вне веток, их собирает
    # git gc; другой клон, shallow. Тогда разницы не построить — полная загрузка
    files, modules, lost = plan_from_state(repo, src, source_root, state, head_sha, platform.ib, full, extension)
    res = _load(platform, run_dir, Path(source_root) / src, files, extension, state, new_state, modules)
    if lost:
        res["action"] += f" (коммита прошлой загрузки {state['commit'][:10]} в репозитории нет)"
    return {**res, "created": True} if created else res


def _commit_exists(repo, sha):
    try:
        gitdiff.rev(repo, sha)
        return True
    except gitdiff.GitError:
        return False


def build_dirs(plan, platform, run_dir, extension=None, full=False, assume_current=False):
    """Сборка без git: контракт — в копии ИБ уже выгрузка plan["baseDir"]; загружаются отличия plan["headDir"]
    (без baseDir или full — полная загрузка)."""
    platform.lock()
    if assume_current:
        state = load_state(run_dir, platform.ib)
        new_state = {"ib": str(platform.ib), "commit": None, "tree": None,
                     "dir": plan["headDir"], "extension": extension}
        save_state(run_dir, {**(state or {}), **new_state}, platform.ib)
        return {"action": "marked", "commit": None}
    created = ensure_ib(platform, extension)
    state = None if created else load_state(run_dir, platform.ib)
    new_state = {"ib": str(platform.ib), "commit": None, "tree": None, "dir": plan["headDir"], "extension": extension}
    files = None if full or created else plan_load_dirs(plan)
    modules = edt_modules(_dir_changes(plan), layout.get(plan.get("sourceFormat")), extension) if files else None
    res = _load(platform, run_dir, Path(plan["headDir"]), files, extension, state, new_state, modules)
    if created:
        return {**res, "created": True}
    return {**res, "contract": "в копии ИБ — «было» (--base-dir)"} if files is not None else res


def loadable(platform, run_dir, dump):
    """(выгрузка Конфигуратора для загрузки, секунды экспорта): исходники EDT — экспорт 1cedtcli (edt.py)."""
    if layout.detect(dump) is not layout.EDT:
        return dump, 0.0
    return edt.export(platform.edtcli, dump, run_dir, platform.edt_timeout)


def _from_ib(platform, run_dir, source, files, modules):
    """Каталог частичной загрузки модулей EDT без экспорта и секунды выгрузки ИБ. InfraError — не собрать:
    модуль формы без Form.xml не загружается — платформа стирает содержимое формы (ib-build)."""
    names = edt.form_names(modules)
    if names is None:
        raise InfraError("форма неизвестного вида")
    target = Path(run_dir) / edt.IB_DIR / "load"
    sec = edt.from_ib(platform, source, target, names, modules, label="edt-ib-load")
    missing = [f for f in files if not (target / f).is_file()]
    if missing:
        raise InfraError(f"после частичной выгрузки ИБ нет {missing[0]}", code="platform-failed")
    return target, sec


def _check_lfs(dump, files):
    """InfraError, если в загружаемой выгрузке указатели Git LFS вместо содержимого: платформа на них падает
    с непонятной «Ошибка формата потока». files — частичная загрузка, None — всё дерево."""
    found = workspace.lfs_pointers(dump, files)
    if found:
        raise InfraError(f"в выгрузке {dump} файлов-указателей Git LFS без содержимого: {len(found)} (например "
                         f"{found[0]}) — объекты LFS не получены: git lfs pull (для другой ветки — "
                         "git lfs fetch <remote> <ветка>)")


def _load(platform, run_dir, dump, files, extension, state, new_state, modules=None):
    """Загрузка выгрузки dump в копию ИБ: files — частичная (пусто — нечего загружать), None — полная.
    modules — пути EDT, если изменены только модули (edt_modules): загрузка без экспорта, не вышло — экспорт."""
    if files is not None and not files:
        save_state(run_dir, {**(state or {}), **new_state}, platform.ib)
        return {"action": "up-to-date", "commit": new_state["commit"]}
    src_layout = layout.detect(dump)
    _check_lfs(dump, files if src_layout is layout.DESIGNER else None)
    result = {"action": "full" if files is None else "partial", "files": len(files or [])}
    if files is None and not platform.ibcmd_usable():
        result["loadNote"] = "полная загрузка Конфигуратором: ibcmd нет, с ним втрое быстрее"
    loaded = None
    if files and modules:
        try:
            loaded, result["ibDump"] = _from_ib(platform, run_dir, dump, files, modules)
        except InfraError as e:
            result["edtNote"] = f"без экспорта EDT не вышло ({e}) — экспорт"
    if loaded is None:
        loaded, exported = loadable(platform, run_dir, dump)
        if exported:
            result["export"] = exported
        if src_layout is layout.EDT:
            result["dump"] = str(loaded)    # выгрузка экспорта — описания для заимствования (extcheck)
    dump = loaded
    lst = Path(run_dir) / "load.list"
    if files is None:
        rc, sec, out = platform.full_load(dump, extension)
    else:
        lst.write_text("\n".join(files) + "\n", encoding="utf-8-sig")
        rc, sec, out = platform.load_files(dump, lst, extension, label="load")
    result["load"] = sec
    if rc and "запрещено" in out and not extension:
        # конфигурация копии на поддержке с запретом изменений: копия одноразовая — снимаем поддержку
        rc2, _, out2 = platform.disable_support()
        if rc2:
            raise InfraError(f"снятие с поддержки: rc={rc2} {out2[-300:]}", code="platform-failed")
        result["supportDisabled"] = True
        if files is None:
            rc, sec, out = platform.full_load(dump, extension)
        else:
            rc, sec, out = platform.load_files(dump, lst, label="load-retry", timeout=7200)
        result["load"] += sec
    if rc:
        errors = source_errors(out, dump, src_layout)
        if errors:
            raise LoadError(errors)
        raise InfraError(f"загрузка конфигурации: rc={rc} {out[-500:]}", code="platform-failed")
    rc, sec, out = platform.update_db(extension)
    result["update"] = sec
    if rc:
        raise InfraError(f"обновление конфигурации БД: rc={rc} {out[-500:]}", code="platform-failed")
    save_state(run_dir, {**(state or {}), **new_state}, platform.ib)
    return result
