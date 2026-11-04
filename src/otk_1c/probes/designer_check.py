"""Полная проверка модулей Конфигуратором (спецификация ci-integration, design.md §9).

    otk designer-check [--repo .] [--no-control]

1. Копия ИБ приводится к редакции HEAD (build: частичная или полная загрузка).
2. Отрицательный контроль — что проверка Конфигуратором работает, а не зелёная впустую:
   А. ИБ контроля в кэше пользователя (одна на версию платформы): пустая конфигурация с серверным модулем
      с заведомой ошибкой и расширение с таким же; `/CheckModules -Server` без расширения и с `-Extension`
      обязан показать обе ошибки — секунды вместо проверки всей копии (ERP — 10 мин).
   Б. А недоступна (ИБ не создаётся, лицензия) или не сработала — заведомая ошибка грузится в серверный общий
      модуль копии (частичная загрузка, рабочее дерево не меняется; копия на поддержке без права изменения
      снимается с поддержки), полная проверка обязана её показать (из результата исключается), затем модуль
      восстанавливается. Не найдена — код 2 «проверка Конфигуратором не работает».
3. `/CheckModules` со всеми режимами и `-ExtendedModulesCheck`. Режим «Толстый клиент (обычное
   приложение)» — только если основной режим запуска конфигурации — обычное приложение или в проекте
   включено environment.ordinaryApplication (флаг «Использовать управляемые формы в обычном приложении»
   об этом не говорит: у управляемых конфигураций он часто включён). Явный [designerCheck] modes главнее
   и проверяется: неизвестный ключ Конфигуратор молча пропускает, а без режима отвечает «ошибок нет»,
   ничего не проверив. rc≠0 без разобранных ошибок — код 2 (не проверено), а не 1.
4. Сверка с базовой линией — файлом принятых ошибок в репозитории ([designerCheck] baseline, по умолчанию
   otk-designer-check.baseline.json): типовая конфигурация проверку всех модулей без ошибок обычно не проходит
   (отраслевая — около 500 в режимах веб-клиента и обычного приложения). Ключ ошибки — объект, текст и строка
   кода без номера строки, поэтому правки выше по модулю его не сдвигают. Падение — только на новых;
   исправленные отмечаются в сводке. `otk designer-check --update-baseline` принимает текущее состояние.

Результат — designer-check.md, designer-check.json, junit-designer-check.xml в каталоге прогона; код 0/1/2 как у gate.
"""
import copy
import json
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

from .. import config, lang, model
from ..errors import InfraError
from ..paths import cache_root
from ..sources import gitdiff, layout
from . import build as build_mod, ib_tools
from .edt import BOM, pid_alive
from .platform import Platform, check_mode, check_modes, platform_version

MODES = check_modes()
CONTROL_PROC = "ОТКОтрицательныйКонтроль"
_LINE = re.compile(r"^\{(?P<obj>[^{}()]+?)\((?P<line>\d+),(?P<col>\d+)\)\}:\s*(?P<msg>.+?)\s*$")
_MODE = re.compile(r"\(Проверка:\s*(.+)\)\s*$")
BASELINE = "otk-designer-check.baseline.json"


def _folders():
    """'ОбщийМодуль' -> 'CommonModules' и т.п. по словарю языка."""
    return {names["single"]: folder for folder, names in lang.dictionary("Russian")["metadataTypes"].items()
            if names.get("single")}


def module_file(obj, folders=None, lay=layout.DESIGNER):
    """Имя модуля в сообщении Конфигуратора -> путь в выгрузке или None.

    ОбщийМодуль.X.Модуль -> CommonModules/X/Ext/Module.bsl; Документ.Д.Форма.Ф.Форма ->
    Documents/Д/Forms/Ф/Ext/Form/Module.bsl; ОбщаяФорма.Ф.Форма -> CommonForms/Ф/Ext/Form/Module.bsl;
    Документ.Д.Команда.К.МодульКоманды -> Documents/Д/Commands/К/Ext/CommandModule.bsl (раскладка — у lay)."""
    folders = folders or _folders()
    parts = obj.split(".")
    if len(parts) < 3 or parts[0] not in folders:
        return None
    folder, name = folders[parts[0]], parts[1]
    kinds = lang.platform_texts()["modules"]
    words = lang.dictionary("Russian")["words"]
    if folder == "CommonForms" and kinds.get(parts[-1]) == "FormModule":
        return lay.module_path(layout.Addr(folder, name, "FormModule"))
    if len(parts) == 5 and parts[2] == words["form"] and kinds.get(parts[4]) == "FormModule":
        return lay.module_path(layout.Addr(folder, name, "FormModule", parts[3]))
    if len(parts) == 5 and parts[2] == words["command"]:
        return lay.module_path(layout.Addr(folder, name, "CommandModule", parts[3]))
    if len(parts) == 3 and kinds.get(parts[2]) in layout.OBJECT_KINDS + ("CommandModule",):
        return lay.module_path(layout.Addr(folder, name, kinds[parts[2]]))
    return None


def parse(output, src="", lay=layout.DESIGNER):
    """Ошибки Конфигуратора: [{object, file, line, col, message, modes}], одинаковые по режимам — одной записью;
    file — путь модуля в исходниках формата lay."""
    folders = _folders()
    found, order = {}, []
    lines = output.splitlines()
    for i, ln in enumerate(lines):
        m = _LINE.match(ln.strip())
        if not m:
            continue
        mode, code = None, ""
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not _LINE.match(nxt.strip()):
            mm = _MODE.search(nxt)
            mode = mm.group(1).strip() if mm else None
            code = " ".join((nxt[:mm.start()] if mm else nxt).replace("<<?>>", "").split())
        obj = m["obj"].split(" ", 1)[-1]          # «демо ОбщийМодуль.X.Модуль» — модуль расширения
        key = (obj, int(m["line"]), int(m["col"]), m["msg"])
        if key not in found:
            rel = module_file(obj, folders, lay)
            found[key] = {"object": obj, "file": f"{src.strip('/')}/{rel}" if rel and src else rel,
                          "line": int(m["line"]), "col": int(m["col"]), "message": m["msg"], "code": code,
                          "modes": []}
            order.append(key)
        if mode and mode not in found[key]["modes"]:
            found[key]["modes"].append(mode)
    return [found[k] for k in order]


ORDINARY = check_mode("OrdinaryClient")


def default_modes(source_root, ordinary_setting=False):
    """Режимы проверки всех модулей по свойствам конфигурации (выгрузка основной конфигурации source_root)."""
    lay = layout.detect(source_root) if source_root else None
    run_mode = lay.read_properties(source_root).run_mode if lay else None
    ordinary = ordinary_setting or run_mode == "OrdinaryApplication"
    return [x for x in MODES if x != ORDINARY or ordinary]


# /CheckConfig (8.3.26–8.5.4, справка «Команды пакетного режима запуска конфигуратора»): проверки конфигурации,
# не зависящие от свойств; режимы модулей — как у /CheckModules плюс клиент-серверные варианты
CONFIG_CHECKS = ("-ConfigLogIntegrity", "-IncorrectReferences", "-HandlersExistence", "-UnreferenceProcedures",
                 "-EmptyHandlers")
SERVER_VARIANTS = {"-ExternalConnection": ["-ExternalConnectionServer"],
                   "-ThinClient": ["-ThickClientManagedApplication", "-ThickClientServerManagedApplication"],
                   ORDINARY: ["-ThickClientServerOrdinaryApplication"]}


def config_check_flags(source_root, ordinary_setting=False, modes=None):
    """Флаги полной проверки /CheckConfig — все применимые к конфигурации (второй эталон паритета).
    modes — режимы /CheckModules (по умолчанию default_modes). Мобильные приложения и -UnsupportedFunctional —
    только при назначении «Приложение для мобильной платформы»; поиск модальных и синхронных вызовов — только если
    конфигурация их не использует (режим не Use). Не включаются: мобильный клиент (по свойствам конфигурации
    не видно, готова ли она к нему; /CheckModules его тоже не проверяет), -DistributiveModules (поставка без
    исходных текстов — настройка поставки, а не исходников), -MobileClientDigiSign (подпись мобильного клиента)."""
    lay = layout.detect(source_root) if source_root else None
    props = lay.read_properties(source_root) if lay else layout.Properties()
    modes = list(modes or default_modes(source_root, ordinary_setting))
    flags = list(CONFIG_CHECKS)
    for m in modes:
        flags += [m] + [v for v in SERVER_VARIANTS.get(m, []) if v not in modes]
    if props.mobile_app:
        flags += ["-MobileAppClient", "-MobileAppServer", "-UnsupportedFunctional"]
    if "-ExtendedModulesCheck" in modes:
        flags += ["-CheckUseModality"] if props.modality in ("DontUse", "UseWithWarnings") else []
        flags += ["-CheckUseSynchronousCalls"] if props.sync_calls in ("DontUse", "UseWithWarnings") else []
    return list(dict.fromkeys(flags))


def baseline_key(f):
    return f"{f['object']}|{f['message']}|{f.get('code', '')}"


def load_baseline(path):
    """Счётчик ключей принятых ошибок (одинаковые строки кода в модуле могут повторяться)."""
    if not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Counter(baseline_key(f) for f in data["findings"])


def save_baseline(path, findings, commit):
    rows = [{k: f[k] for k in ("object", "message", "code", "file")} for f in findings]
    rows.sort(key=lambda r: (r["object"], r["message"], r["code"]))
    Path(path).write_text(json.dumps({"commit": commit, "count": len(rows), "findings": rows}, ensure_ascii=False,
                                     indent=1) + "\n", encoding="utf-8", newline="\n")


def split_new(findings, baseline):
    """(новые, принятые, сколько принятых исчезло)."""
    if baseline is None:
        return list(findings), [], 0
    left = baseline.copy()
    new, known = [], []
    for f in findings:
        k = baseline_key(f)
        if left[k] > 0:
            left[k] -= 1
            known.append(f)
        else:
            new.append(f)
    return new, known, sum(v for v in left.values() if v > 0)


def control_module(dump):
    """Серверный общий модуль для отрицательного контроля: ОбщегоНазначения, иначе первый серверный; исходники —
    в любом формате."""
    lay = layout.detect(dump) or layout.DESIGNER
    cands = sorted(lay.object_names(dump, "CommonModules"), key=lambda n: n != "ОбщегоНазначения")
    for name in cands:
        flags = lay.module_flags(layout.read_text(Path(dump) / lay.object_file("CommonModules", name)))
        module = Path(dump) / lay.module_path(layout.Addr("CommonModules", name, "Module"))
        if (flags["Server"] and not flags["Global"] and not flags["ClientManagedApplication"] and module.exists()
                and not lay.protected(module.read_bytes())):
            return name
    return None


BROKEN = f"Процедура {CONTROL_PROC}()\r\n\tА = ;\r\nКонецПроцедуры\r\n"
CONTROL_MAIN = "ОТККонтроль"                 # модуль основной конфигурации ИБ контроля
CONTROL_EXT = "otkc_control"                 # её расширение и модуль в нём: проверка с -Extension
CONTROL_EXT_MODULE = "otkc_Контроль"
MOBILE_MODES = ["-MobileAppClient", "-MobileAppServer", "-MobileClient"]   # допустимы явно, по умолчанию не входят
CONTROL_TIMEOUT = 300                        # шаги ИБ контроля — секунды; зависание (диалог, лицензия) — к варианту Б
_FORBIDDEN = re.compile(r"редактирование объекта метаданных .* запрещено", re.I)


def control_ib_dir(platform):
    """Каталог ИБ контроля (вариант А) в кэше пользователя: своя на каждую версию платформы."""
    return Path(cache_root()) / "control" / (platform_version(platform.bin) or "platform")


def _on(platform, ib, run_dir):
    """Тот же Конфигуратор на другой файловой ИБ, без пользователя (у ИБ контроля их нет)."""
    p = copy.copy(platform)
    p.connection, p.ib, p.user, p.password, p.run_dir = None, Path(ib), "", "", Path(run_dir)
    return p


def control_ib(platform, log=print):
    """ИБ контроля (вариант А): пустая конфигурация + серверный общий модуль с заведомой ошибкой и расширение с таким
    же модулем; создаётся один раз на версию платформы. Не создаётся — InfraError (тогда — вариант Б)."""
    root = control_ib_dir(platform)
    if (root / "ready").is_file():
        return root / "ib"
    # остатки создания аварийно завершённых процессов (<версия>.<pid>.tmp)
    for old in root.parent.glob(f"{root.name}.*.tmp") if root.parent.is_dir() else ():
        pid = old.name[len(root.name) + 1:-4]
        if pid.isdigit() and not pid_alive(int(pid)):
            shutil.rmtree(old, ignore_errors=True)
    tmp = root.with_name(f"{root.name}.{os.getpid()}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    log(f"отрицательный контроль: создание ИБ контроля {root}")
    try:
        rc, out = _on(platform, tmp / "ib", tmp).create_file_ib(tmp / "ib", timeout=CONTROL_TIMEOUT)
        if rc:
            raise InfraError(f"файловая ИБ не создаётся: rc={rc} {out[-300:]}", code="platform-failed")
        p = _on(platform, tmp / "ib", tmp)
        dump = tmp / "dump"

        def step(*args, label):
            rc, _, out = p.designer(*args, label=label, timeout=CONTROL_TIMEOUT)
            if rc:
                raise InfraError(f"ИБ контроля, {label}: rc={rc} {out[-300:]}", code="platform-failed")
        step("/DumpConfigToFiles", dump, "-Format", "Hierarchical", label="control-ib-dump")
        cfg = dump / "Configuration.xml"
        text = cfg.read_text(encoding="utf-8-sig")
        cfg.write_text(text.replace("</ChildObjects>", f"<CommonModule>{CONTROL_MAIN}</CommonModule></ChildObjects>",
                                    1), encoding="utf-8-sig")
        ib_tools.write_common_module(dump, CONTROL_MAIN, {"Server": "true"}, BROKEN)
        step("/LoadConfigFromFiles", dump, "-Format", "Hierarchical", label="control-ib-load")
        step("/UpdateDBCfg", label="control-ib-update")
        ext = ib_tools.generate_test_extension(tmp / "ext", ib_tools.base_properties(dump), name=CONTROL_EXT,
                                            prefix="otkc_", modules={CONTROL_EXT_MODULE: ({"Server": "true"}, BROKEN)})
        step("/LoadConfigFromFiles", ext, "-Extension", CONTROL_EXT, label="control-ib-ext")
        (tmp / "ready").write_text(platform_version(platform.bin) or "", encoding="utf-8")
        if (root / "ready").is_file():                      # параллельный запуск успел раньше — его ИБ не трогать
            return root / "ib"
        shutil.rmtree(root, ignore_errors=True)
        try:
            tmp.rename(root)
        except OSError:                                     # параллельный запуск успел раньше
            if not (root / "ready").is_file():
                raise InfraError(f"ИБ контроля: не удалось переместить {tmp} в {root}") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return root / "ib"


def minimal_control(platform, run_dir, log=print):
    """Вариант А: /CheckModules -Server в ИБ контроля — без расширения и с -Extension — обязан показать ошибку
    в контрольном модуле. Подтверждает запуск Конфигуратора, лицензию, разбор вывода и язык платформы.
    (сработал, описание); ИБ контроля не создаётся — InfraError."""
    p = _on(platform, control_ib(platform, log), run_dir)
    parts, ok = [], True
    for label, args, obj in (("control-check", (), f"ОбщийМодуль.{CONTROL_MAIN}.Модуль"),
                             ("control-check-ext", ("-Extension", CONTROL_EXT),
                              f"ОбщийМодуль.{CONTROL_EXT_MODULE}.Модуль")):
        rc, sec, out = p.designer("/CheckModules", "-Server", *args, label=label, timeout=CONTROL_TIMEOUT)
        hit = bool(rc) and any(f["object"] == obj for f in parse(out))
        ok = ok and hit
        parts.append(f"{'расширение' if args else 'конфигурация'}: rc={rc}, ошибка {'найдена' if hit else 'НЕ найдена'}"
                     f" ({sec} c)")
    if not ok:
        shutil.rmtree(control_ib_dir(platform), ignore_errors=True)   # следующий запуск создаст её заново
    return ok, "ИБ контроля — " + "; ".join(parts)


def _invalidate_build(platform, run_dir):
    """Состояние копии ИБ неизвестно — следующая сборка полная."""
    build_mod.state_path(run_dir, platform.ib).unlink(missing_ok=True)


def inline_load(platform, dump, run_dir, ext_args=(), log=print):
    """Вариант Б: заведомая ошибка в серверном общем модуле копии перед полной проверкой. Возвращает {name, rel,
    line, list}; не загрузилась — InfraError. Загрузку не пускает поддержка (конфигурация поставщика без права
    изменения) — копия своя: снимается с поддержки (/ManageCfgSupport -disableSupport -force) и повтор.
    dump в любом формате: модуль грузится в пути выгрузки, исходный — из копии в каталоге прогона (исходники EDT
    в ИБ не грузятся)."""
    name = control_module(dump)
    if not name:
        raise InfraError("нет серверного общего модуля для отрицательного контроля")
    addr = layout.Addr("CommonModules", name, "Module")
    rel = layout.DESIGNER.module_path(addr)
    original = (Path(dump) / (layout.detect(dump) or layout.DESIGNER).module_path(addr)).read_bytes()
    original = original if original.startswith(BOM) else BOM + original
    tmp, keep = Path(run_dir) / "control", Path(run_dir) / "control-restore"
    for d, data in ((tmp, original + ("\r\n" + BROKEN).encode("utf-8")), (keep, original)):
        shutil.rmtree(d, ignore_errors=True)
        (d / rel).parent.mkdir(parents=True)
        (d / rel).write_bytes(data)
    line = layout.decode(original).count("\n") + 3
    lst = Path(run_dir) / "control.list"
    lst.write_text(rel + "\n", encoding="utf-8-sig")
    log(f"отрицательный контроль (внутри проверки): ОбщийМодуль.{name}, строка {line}")
    ext = ext_args[1] if ext_args else None
    rc, _, out = platform.load_files(tmp, lst, ext, label="control-load")
    if rc and _FORBIDDEN.search(out) and not ext_args:
        log("копия ИБ на поддержке без права изменения — снятие с поддержки")
        rc, _, out = platform.disable_support(label="disable-support")
        if not rc:
            rc, _, out = platform.load_files(tmp, lst, ext, label="control-load")
    if rc:
        _invalidate_build(platform, run_dir)
        raise InfraError(f"контрольный модуль не загрузился: rc={rc} {out[-300:]}", code="platform-failed")
    return {"name": name, "rel": rel, "line": line, "list": lst, "restore": keep}


def inline_restore(platform, run_dir, control, ext_args=()):
    rc, _, out = platform.load_files(control["restore"], control["list"], ext_args[1] if ext_args else None,
                                     label="control-restore")
    if rc:
        _invalidate_build(platform, run_dir)
        raise InfraError(f"контрольный модуль не восстановлен: rc={rc} {out[-300:]}", code="platform-failed")


def modes_of(cfg, source_root):
    """Режимы проверки всех модулей: [designerCheck] modes или по свойствам конфигурации (default_modes).
    Явные режимы проверяются: Конфигуратор молча пропускает неизвестный ключ, а без ключа режима отвечает
    «Синтаксических ошибок не обнаружено!», ничего не проверив — это ошибка настройки (InfraError)."""
    modes = cfg.get("designerCheck", {}).get("modes")
    if modes is None:
        return default_modes(source_root, cfg["environment"].get("ordinaryApplication", False))
    if isinstance(modes, str):
        modes = modes.split()
    # -AllExtensions не принимается: без расширений проверяет ничего (rc=0); проект-расширение — project.extension
    known = {k.lower(): k for k in MODES + MOBILE_MODES}
    bad = [m for m in modes if str(m).lower() not in known]
    contexts = [m for m in modes if str(m).lower() in known and known[str(m).lower()] != "-ExtendedModulesCheck"]
    if bad or not contexts:
        raise InfraError(f"[designerCheck] modes = {modes}: " + (f"неизвестные ключи {bad}" if bad else
                         "нет ни одного режима") + f"; допустимы {', '.join(MODES)}", code="config-invalid")
    return [known[str(m).lower()] for m in modes]


def baseline_of(project_dir, cfg):
    return Path(project_dir) / cfg.get("designerCheck", {}).get("baseline", BASELINE)


def check(platform, dump, src, modes, baseline_path, run_dir, ext_args=(), control=True, log=print,
          update_baseline=False, commit=None):
    """Отрицательный контроль и проверка всех модулей в уже собранной копии ИБ (dump — загруженная в неё
    выгрузка, src — её путь от корня репозитория для файлов находок). Возвращает {code, control, rc, findings
    (новые относительно базовой линии), known, fixed, baseline, timings, notes}; InfraError — наружу."""
    res = {"code": 0, "control": None, "rc": None, "findings": [], "known": 0, "fixed": 0, "baseline": None,
           "timings": {}, "notes": []}
    inline = None
    if control:
        t = time.time()
        try:
            ok, detail = minimal_control(platform, run_dir, log)
        except Exception as e:  # noqa: BLE001 — любой сбой ИБ контроля (кэш, выгрузка, лицензия) — к варианту Б
            ok, detail = False, f"ИБ контроля недоступна: {e}"
        res["timings"]["control"] = round(time.time() - t, 1)
        res["control"] = {"ok": ok, "variant": "A", "detail": detail}
        if not ok:
            # вариант Б: заведомая ошибка в самой копии, её обязана показать полная проверка
            res["control"] = {"ok": False, "variant": "B", "detail": f"{detail}; контроль внутри полной проверки"}
            if getattr(platform, "connection", None):
                res["code"] = 2
                res["notes"].append(f"проверка Конфигуратором не подтверждена: {detail}; контроль внутри проверки — "
                                    "только в своей копии ИБ (environment.ib), не в рабочей базе")
                return res
            log(f"отрицательный контроль: {detail} — контроль внутри полной проверки")
            t = time.time()
            try:
                inline = inline_load(platform, dump, run_dir, ext_args, log)
            except InfraError as e:
                raise InfraError(f"{e} (вариант Б после: {detail})", code=e.code) from None
            res["timings"]["control"] += round(time.time() - t, 1)
    log(f"полная проверка: {' '.join(modes)}")
    try:
        rc, sec, out = platform.designer("/CheckModules", *modes, *ext_args, label="designer-check",
                                         timeout=6 * 3600)
    finally:
        if inline:
            t = time.time()
            inline_restore(platform, run_dir, inline, ext_args)
            res["timings"]["control"] += round(time.time() - t, 1)
    res["timings"]["check"] = sec
    res["rc"] = rc
    found = parse(out, src, layout.detect(dump) or layout.DESIGNER)
    if inline:
        obj = f"ОбщийМодуль.{inline['name']}.Модуль"
        mine = [f for f in found if f["object"] == obj and f["line"] == inline["line"]]
        found = [f for f in found if f not in mine]
        res["control"]["ok"] = bool(mine)
        res["control"]["detail"] += f": {obj}, строка {inline['line']} — ошибка {'найдена' if mine else 'НЕ найдена'}"
        if not mine:
            same = [f"{f['line']}: {f['message']}" for f in found if f["object"] == obj]
            res["code"] = 2
            res["notes"].append("проверка Конфигуратором не работает: " + res["control"]["detail"]
                                + (f" (в модуле есть другая ошибка — платформа показывает первую: {same[0]})"
                                   if same else ""))
            return res
    if rc and not found and not inline:             # при контроле Б rc≠0 даёт сама контрольная ошибка
        res["code"] = 2
        res["notes"].append(f"Конфигуратор: rc={rc} без разобранных ошибок: {out[-500:]}")
        return res
    if update_baseline:
        save_baseline(baseline_path, found, commit)
        res["notes"].append(f"базовая линия обновлена: {baseline_path.name}, ошибок {len(found)} — закоммитить")
    baseline = load_baseline(baseline_path)
    new, known, fixed = split_new(found, baseline)
    res.update(findings=new, known=len(known), fixed=fixed,
               baseline=baseline_path.name if baseline is not None else None)
    res["code"] = 1 if new else 0
    return res


def run(repo, cfg, run_dir, control=True, log=print, update_baseline=False):
    """Сборка HEAD, контроль, полная проверка. Возвращает сводку (code — код выхода)."""
    env, project = cfg["environment"], cfg["project"]
    src = project["source"]
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    platform = Platform(env, run_dir)
    ext = project.get("extension")
    ext_args = ["-Extension", ext] if ext else []
    head = gitdiff.rev(repo, "HEAD")
    baseline_path = baseline_of(repo, cfg)
    summary = {"code": 0, "commit": head, "modes": [], "control": None, "findings": [], "known": 0,
               "fixed": 0, "baseline": None, "timings": {}, "notes": []}
    t = time.time()
    try:
        modes = summary["modes"] = modes_of(cfg, config.parent_dir(repo, cfg) or Path(repo) / src)
        summary["build"] = build_mod.build(repo, head, src, repo, platform, run_dir, extension=ext)
        summary["timings"]["build"] = round(time.time() - t, 1)
        res = check(platform, Path(repo) / src, src, modes, baseline_path, run_dir, ext_args, control, log,
                    update_baseline, head)
        summary["timings"].update(res.pop("timings"))
        summary["notes"] += res.pop("notes")
        summary.update(res)
    except InfraError as e:
        summary["code"] = 2
        summary["notes"].append(f"сбой инфраструктуры: {e}")
    return _finish(summary, run_dir)


def verdict(summary):
    n = len(summary["findings"])
    return {0: "OK: полная проверка Конфигуратором без ошибок",
            1: f"ОШИБКИ: полная проверка Конфигуратором — {'новых ' if summary.get('baseline') else ''}{n}",
            2: "СБОЙ: " + (summary["notes"][0] if summary["notes"] else "инфраструктура")}[summary["code"]]


def markdown(summary):
    lines = [f"# {verdict(summary)}", "", f"Коммит `{summary['commit'][:10]}`, режимы: {' '.join(summary['modes'])}"]
    if summary.get("control"):
        lines.append(f"Отрицательный контроль ({summary['control'].get('variant', 'A')}): "
                     f"{'сработал' if summary['control']['ok'] else 'НЕ СРАБОТАЛ'} — {summary['control']['detail']}")
    if summary.get("baseline"):
        lines.append(f"Базовая линия `{summary['baseline']}`: принятых ошибок {summary['known']}, "
                     f"исправлено {summary['fixed']}")
    if summary["findings"]:
        title = "Новые ошибки" if summary.get("baseline") else "Ошибки"
        lines += ["", f"## {title} ({len(summary['findings'])})"]
        for f in summary["findings"]:
            where = f"`{f['file']}:{f['line']}`" if f["file"] else f"`{f['object']}({f['line']},{f['col']})`"
            lines.append(f"- {where} — {f['message']} _({', '.join(f['modes']) or 'режим не указан'})_")
    if summary["notes"]:
        lines += ["", "## Примечания"] + [f"- {n}" for n in summary["notes"]]
    lines += ["", "## Время, с", "| Шаг | Время |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in summary["timings"].items()]
    return "\n".join(lines) + "\n"


def junit(summary):
    cases = []
    for f in summary["findings"]:
        name = escape(f"{f['file'] or f['object']}:{f['line']}")
        esc_msg = escape(f["message"], {chr(34): "&quot;"})
        cases.append(f'  <testcase classname="designer-check" name="{name}"><failure message="{esc_msg}">'
                     f"{escape(', '.join(f['modes']))}</failure></testcase>")
    if summary["code"] == 2:
        esc_msg = escape(verdict(summary), {chr(34): "&quot;"})
        cases.append(f'  <testcase classname="designer-check" name="infrastructure">'
                     f'<error message="{esc_msg}"/></testcase>')
    if not cases:
        cases.append('  <testcase classname="designer-check" name="CheckModules"/>')
    fails = len(summary["findings"])
    errors = 1 if summary["code"] == 2 else 0
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="otk-designer-check" tests="{len(cases)}" '
            f'failures="{fails}" errors="{errors}">\n' + "\n".join(cases) + "\n</testsuite>\n")


def _finish(summary, run_dir):
    data = {**model.header("designer-check", internal=True), **summary}
    (run_dir / "designer-check.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "designer-check.md").write_text(markdown(summary), encoding="utf-8")
    (run_dir / "junit-designer-check.xml").write_text(junit(summary), encoding="utf-8")
    return summary
