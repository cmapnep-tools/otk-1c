"""Доработки относительно конфигурации поставщика ИБ — разовая проверка `gate --vendor`.

«Стало» — полная выгрузка основной конфигурации ИБ (кэш ОТК по базе, повторно — `-update`), «было» — зеркало
«стало», в котором модули из полного отчёта `/CompareCfg` (поставщик -> основная) возвращены к тексту поставщика,
а объекты «только в основной конфигурации» отсутствуют. Состояние поддержки — по заголовку
`Ext/ParentConfigurations.bin` выгрузки корня ИБ: файлам репозитория не доверяем (их затирают).

Устройство отчёта (8.3.27, по опыту):
- номера строк — сквозная нумерация общего вида «бок о бок»: общая строка, строка только первой или только второй
  получает номер; кусок «Изменено: a - b» занимает max(строк первой, строк второй) номеров;
- метки «<» (первая) и «>» (вторая) в кусках «Изменено» ненадёжны (при замене 1 строки тремя строки второй помечены
  «<»), поэтому строки известной стороны в куске определяются по её тексту, остальные — неизвестной стороны;
- табуляция выводится как «·»: модуль с настоящим «·» восстановить нельзя — он проверяется целиком.
"""
import hashlib
import os
import re
import shutil
from pathlib import Path

from ..analyzers import bsl_analyzer
from ..errors import InfraError
from ..paths import cache_root
from ..probes.platform import Platform

# тип объекта в отчёте (рус. единственное) -> каталог выгрузки
COLLECTIONS = {
    "ОбщийМодуль": "CommonModules", "Справочник": "Catalogs", "Документ": "Documents", "Обработка": "DataProcessors",
    "Отчет": "Reports", "РегистрСведений": "InformationRegisters", "РегистрНакопления": "AccumulationRegisters",
    "РегистрБухгалтерии": "AccountingRegisters", "РегистрРасчета": "CalculationRegisters",
    "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes", "ПланСчетов": "ChartsOfAccounts",
    "ПланВидовРасчета": "ChartsOfCalculationTypes", "ПланОбмена": "ExchangePlans", "БизнесПроцесс": "BusinessProcesses",
    "Задача": "Tasks", "Перечисление": "Enums", "ЖурналДокументов": "DocumentJournals", "Константа": "Constants",
    "ОбщаяФорма": "CommonForms", "ОбщаяКоманда": "CommonCommands", "HTTPСервис": "HTTPServices",
    "WebСервис": "WebServices", "ХранилищеНастроек": "SettingsStorages", "КритерийОтбора": "FilterCriteria",
    "СервисИнтеграции": "IntegrationServices", "Роль": "Roles", "Подсистема": "Subsystems",
    "ОбщийМакет": "CommonTemplates", "ОбщаяКартинка": "CommonPictures", "ПодпискаНаСобытие": "EventSubscriptions",
    "РегламентноеЗадание": "ScheduledJobs", "ОбщийРеквизит": "CommonAttributes",
    "ФункциональнаяОпция": "FunctionalOptions", "ПараметрФункциональныхОпций": "FunctionalOptionsParameters",
    "ОпределяемыйТип": "DefinedTypes", "ПараметрСеанса": "SessionParameters", "ГруппаКоманд": "CommandGroups",
    "Стиль": "Styles", "ЭлементСтиля": "StyleItems", "Язык": "Languages", "XDTOПакет": "XDTOPackages",
    "WSСсылка": "WSReferences", "Последовательность": "Sequences", "ВнешнийИсточникДанных": "ExternalDataSources",
}
# свойство-модуль объекта -> файл в Ext объекта
OBJECT_MODULES = {
    "Модуль": "Module.bsl", "Модуль объекта": "ObjectModule.bsl", "Модуль менеджера": "ManagerModule.bsl",
    "Модуль набора записей": "RecordSetModule.bsl", "Модуль менеджера значения": "ValueManagerModule.bsl",
    "Модуль команды": "CommandModule.bsl",
}
ROOT_MODULES = {
    "Модуль сеанса": "Ext/SessionModule.bsl", "Модуль управляемого приложения": "Ext/ManagedApplicationModule.bsl",
    "Модуль обычного приложения": "Ext/OrdinaryApplicationModule.bsl",
    "Модуль внешнего соединения": "Ext/ExternalConnectionModule.bsl",
}
HUNK = re.compile(r"^(Изменено|Объект присутствует только в (конфигурации поставщика|основной конфигурации|"
                  r"первой конфигурации|второй конфигурации)): (\d+) - (\d+)$")
OBJECT = re.compile(r"^(\*\*\*|<--|-->)?\^?([^\s.][^\s]*\.[^\s]+)$")

LOCKED, EDITABLE, NONE = "locked", "editable", "none"
STATE_TEXT = {LOCKED: "на поддержке, изменения запрещены", EDITABLE: "на поддержке, изменения разрешены",
              NONE: "не на поддержке"}


def support_state(header):
    """Состояние поддержки по заголовку ParentConfigurations.bin ({6,1,1,…} / {6,0,1,…} / {6,0,0,0,1,0});
    None или пусто — конфигурация не на поддержке."""
    m = re.match(r"\{(\d+),(\d+),(\d+)", (header or "").lstrip("﻿"))
    if not m or m.group(3) == "0":
        return NONE
    return LOCKED if m.group(2) == "1" else EDITABLE


def root_info(root):
    """(состояние, имя, версия) по выгрузке корня ИБ (Configuration.xml и Ext/ParentConfigurations.bin)."""
    root = Path(root)
    pc = root / "Ext" / "ParentConfigurations.bin"
    header = pc.read_bytes()[:200].decode("utf-8", "replace") if pc.exists() else ""
    text = (root / "Configuration.xml").read_text(encoding="utf-8-sig")
    def tag(t):
        return m.group(1) if (m := re.search(rf"<{t}>([^<]*)</{t}>", text)) else ""
    return support_state(header), tag("Name"), tag("Version")


def _text(s):
    s = s.strip()
    if s[:1] in "<>":
        s = s[1:].strip()
    return s[1:-1].replace("·", "\t") if len(s) >= 2 and s[0] == s[-1] == '"' else s


def read_report(path):
    data = Path(path).read_bytes()
    enc = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    return data.decode(enc).splitlines()


def parse(lines):
    """Полный отчёт -> {"modules": [(объект, свойство, куски)], "added": [объект], "vendorOnly": [объект],
    "changed": [объект]}; кусок — (вид, a, b, [(метка, текст)]), вид: chg | first | second.
    Объект — строка дерева, вложенная в объект (имя продолжает имя владельца) или в корень «Конфигурация.…»;
    строки под свойствами («Состав», «Тип») — значения свойств, а не объекты."""
    out = {"modules": [], "added": [], "vendorOnly": [], "changed": []}
    stack, cur, hunk = [], None, None            # stack: [(отступ, имя объекта или None)]
    for raw in lines:
        s = raw.strip()
        if s.startswith("- "):
            depth = len(raw) - len(raw.lstrip("\t"))
            while stack and stack[-1][0] >= depth:
                stack.pop()
            body, hunk, cur = s[2:], None, None
            if " - Различаются значения" in body:
                owner = stack[-1][1] if stack else None
                if owner:
                    cur = (owner, body.split(" - ")[0].lstrip("*<->^ "), [])
                    out["modules"].append(cur)
                stack.append((depth, None))
                continue
            m = OBJECT.match(body)
            owner = stack[-1][1] if stack else None
            name = m.group(2) if m else None
            is_obj = bool(name) and (name.startswith("Конфигурация.") and not stack
                                     or owner is not None and (owner.startswith("Конфигурация.")
                                                               or name.startswith(owner + ".")))
            if is_obj:
                kind = {"<--": "added", "-->": "vendorOnly", "***": "changed"}.get(m.group(1))
                if kind and not name.startswith("Конфигурация."):
                    out[kind].append(name)
            stack.append((depth, name if is_obj else None))
        elif cur and (m := HUNK.match(s)):
            where = m.group(2) or ""
            kind = "chg" if m.group(1) == "Изменено" else ("first" if "поставщика" in where or "первой" in where
                                                             else "second")
            hunk = (kind, int(m.group(3)), int(m.group(4)), [])
            cur[2].append(hunk)
        elif hunk and s and s[0] in '<>"':
            hunk[3].append((s[0] if s[0] in "<>" else "", _text(s)))
    return out


def object_paths(name):
    """Пути объекта отчёта в выгрузке Конфигуратора (каталог и файл описания) или [] — не поддержано."""
    parts = name.split(".")
    coll = COLLECTIONS.get(parts[0])
    if not coll or len(parts) < 2 or len(parts) % 2:
        return []
    base = f"{coll}/{parts[1]}"
    if len(parts) == 2:
        return [base, base + ".xml"]
    if parts[0] == "Подсистема" and all(p == "Подсистема" for p in parts[2::2]):
        rel = "/".join([coll, parts[1]] + [f"Subsystems/{p}" for p in parts[3::2]])
        return [rel, rel + ".xml"]
    if len(parts) == 4 and parts[2] == "Форма":
        return [f"{base}/Forms/{parts[3]}", f"{base}/Forms/{parts[3]}.xml"]
    if len(parts) == 4 and parts[2] == "Команда":
        return [f"{base}/Commands/{parts[3]}"]
    if len(parts) == 4 and parts[2] == "Макет":
        return [f"{base}/Templates/{parts[3]}", f"{base}/Templates/{parts[3]}.xml"]
    return []                                     # реквизиты, табличные части — внутри XML владельца


def module_path(obj, prop):
    """Путь модуля в выгрузке Конфигуратора или None (не модуль / не поддержано)."""
    parts = obj.split(".")
    if parts[0] == "Конфигурация":
        return ROOT_MODULES.get(prop)
    coll = COLLECTIONS.get(parts[0])
    if not coll or len(parts) < 2:
        return None
    base = f"{coll}/{parts[1]}"
    if len(parts) == 2:
        if parts[0] == "ОбщаяФорма" and prop == "Модуль":
            return f"{base}/Ext/Form/Module.bsl"
        return f"{base}/Ext/{OBJECT_MODULES[prop]}" if prop in OBJECT_MODULES else None
    if len(parts) == 4 and parts[2] == "Форма" and prop == "Модуль":
        return f"{base}/Forms/{parts[3]}/Ext/Form/Module.bsl"
    if len(parts) == 4 and parts[2] == "Команда" and prop in ("Модуль", "Модуль команды"):
        return f"{base}/Commands/{parts[3]}/Ext/CommandModule.bsl"
    return None


def _subseq(sub, seq):
    it = iter(seq)
    return all(any(x == y for y in it) for x in sub)


def apply(known, hunks, known_side):
    """Строки неизвестной стороны по строкам известной (known_side: first | second) и кускам отчёта."""
    other = "second" if known_side == "first" else "first"
    sure_mark = "<" if known_side == "first" else ">"
    out, pos, row = [], 0, 1
    for kind, a, b, lines in hunks:
        texts = [t for _, t in lines]
        common = a - row
        if common < 0 or pos + common > len(known):
            raise ValueError(f"кусок {a}-{b}: номер вне модуля")
        out += known[pos:pos + common]
        pos, r = pos + common, b - a + 1
        if kind == other:                      # строки только неизвестной стороны
            out += texts
        elif kind == known_side:               # строки только известной — пропустить
            pos += r
        else:
            # метки — только подсказка при выборе из двух подходящих размеров: сами они ошибаются
            sure = [t for mark, t in lines if mark == sure_mark]
            fits = [m for m in sorted({r, len(texts) - r}, reverse=True)
                    if 0 < m < len(texts) and _subseq(known[pos:pos + m], texts)]
            if not fits:
                raise ValueError(f"кусок {a}-{b}: строки известной стороны не найдены")
            m = next((m for m in fits if _subseq(sure, known[pos:pos + m])), fits[0])
            block = known[pos:pos + m]
            rest, it = list(texts), iter(range(len(texts)))
            for line in block:
                for i in it:
                    if rest[i] == line:
                        rest[i] = None
                        break
            out += [t for t in rest if t is not None]
            pos += m
        row = b + 1
    return out + known[pos:]


def restore(path, hunks):
    """Текст поставщика (bytes) модуля основной конфигурации path по кускам отчёта; ValueError — не восстановить."""
    data = Path(path).read_bytes()
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    if any("·" in line for line in lines):
        raise ValueError("в тексте есть «·» — табуляция отчёта неотличима")
    eol = "\r\n" if "\r\n" in text else "\n"
    got = apply(lines, hunks, "second")
    body = eol.join(got) + (eol if text.endswith(("\n", "\r")) else "")
    return (b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b"") + body.encode("utf-8")


def _drop(path):
    if not path.exists() and not path.is_symlink():
        return
    if bsl_analyzer.is_link(path):
        try:
            path.rmdir()
        except OSError:
            path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def build_before(head, dest, report):
    """«Было» в dest — зеркало выгрузки head (ссылки) с модулями поставщика по разобранному отчёту report.
    Возвращает сведения: restored, whole (модули целиком — не восстановлены), added (новые объекты), skipped."""
    head, dest = Path(head), Path(dest)
    info = {"restored": [], "whole": [], "added": [], "skipped": []}
    texts = {}
    for obj, prop, hunks in report["modules"]:
        rel = module_path(obj, prop)
        if not rel:
            if prop.startswith("Модуль"):            # прочие свойства («Длина номера»…) — не код, «было» = ИБ
                info["skipped"].append(f"{obj} / {prop}")
            continue
        if not (head / rel).exists():
            continue                                  # модуль нового объекта или удалён — без восстановления
        try:
            texts[rel] = restore(head / rel, hunks)
        except ValueError as e:
            texts[rel] = None
            info["whole"].append(f"{rel}: {e}")
    drops = []
    for name in report["added"]:
        paths = object_paths(name)
        if not paths:
            info["skipped"].append(name)
        drops += paths
    drops = [p for p in drops if not any(p.startswith(d + "/") for d in drops if d != p)]
    # всё, что меняется, — внутри копий (owners): ссылки ведут в выгрузку «стало», писать через них нельзя
    owners = {tuple(r.split("/")[:2]) for r in texts} | {tuple(r.split("/")[:2]) for r in drops if r.count("/") >= 2}
    skip = {tuple(r.split("/")) for r in drops if r.count("/") == 1}
    bsl_analyzer.mirror(head, dest, owners, skip)
    for r in drops:
        if r.count("/") >= 2:
            _drop(dest / r)
    for rel, data in texts.items():
        target = dest / rel
        if not Path(os.path.realpath(target)).is_relative_to(Path(os.path.realpath(dest))) \
                or target.stat().st_nlink > 1:
            raise InfraError(f"зеркало «было»: {rel} — ссылка на выгрузку ИБ, а не копия", code="config-invalid")
        target.unlink()
        if data is None:
            continue                                  # нет в «было» — модуль целиком новый
        target.write_bytes(data)
        info["restored"].append(rel)
    info["added"] = [n for n in report["added"] if object_paths(n)]
    return info


# --- работа с ИБ ---

def _dump(platform, dest, label, list_file=None, update=False, timeout=7200):
    args = ["/DumpConfigToFiles", dest]
    if list_file:
        args += ["-listFile", list_file]
    if update:
        args += ["-update", "-force"]
    rc, sec, out = platform.designer(*args, timeout=timeout, label=label)
    if rc:
        raise InfraError(f"выгрузка конфигурации ИБ не удалась ({label}, rc={rc}): {out[-300:]}",
                         code="platform-failed")
    return sec


def ib_key(platform):
    return hashlib.sha1(platform.ib_target().lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def prepare(cfg, run_dir, log=print):
    """Выгрузки для gate --vendor: {"head", "base", "info", "notes"}; конфигурация не на поддержке — InfraError."""
    env = cfg["environment"]
    if cfg["project"].get("extension"):
        raise InfraError("--vendor — только для основной конфигурации: у расширения нет конфигурации поставщика",
                         code="config-invalid")
    if not (env.get("ib") or env.get("connection")):
        raise InfraError("--vendor: нужна ИБ с конфигурацией поставщика (--ib или --connection)", code="no-ib")
    work = Path(run_dir) / "vendor"
    cleanup(run_dir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    platform = Platform(env, work)
    root = work / "root"
    (work / "root.lst").write_text("Configuration\n", encoding="utf-8")
    sec_root = _dump(platform, root, "vendor-root", work / "root.lst", timeout=1800)
    state, name, version = root_info(root)
    info = {"state": state, "name": name, "version": version, "timings": {"root": sec_root}}
    log(f"конфигурация ИБ {name} {version}: {STATE_TEXT[state]} ({sec_root} c)")
    if state == NONE:
        raise InfraError(f"--vendor: конфигурация ИБ {name} не на поддержке — конфигурации поставщика в ИБ нет; "
                         "проверить доработки можно с --base-dir (выгрузка поставщика) или по git",
                         code="config-invalid")
    if state == LOCKED:
        return {"head": root, "base": root, "info": info,
                "notes": [f"конфигурация на поддержке без изменений ({name} {version}): доработок нет"]}

    report_file = work / "compare.txt"
    rc, sec, out = platform.designer(
        "/CompareCfg", "-FirstConfigurationType", "VendorConfiguration", "-FirstName", name,
        "-SecondConfigurationType", "MainConfiguration", "-ReportType", "Full", "-IncludeChangedObjects",
        "-IncludeAddedObjects", "-IncludeDeletedObjects", "-ReportFormat", "txt", "-ReportFile", report_file,
        timeout=3600, label="vendor-compare")
    if rc or not report_file.exists():
        raise InfraError(f"/CompareCfg с конфигурацией поставщика не удался (rc={rc}): {out[-300:]}",
                         code="platform-failed")
    info["timings"]["compare"] = sec
    log(f"/CompareCfg: {sec} c")

    head = Path(cache_root()) / "ib-dump" / ib_key(platform)
    update = (head / "ConfigDumpInfo.xml").exists()
    head.parent.mkdir(parents=True, exist_ok=True)
    info["timings"]["dump"] = _dump(platform, head, "vendor-dump", update=update)
    info["dump"] = {"dir": str(head), "update": update}
    log(f"выгрузка ИБ ({'обновление' if update else 'полная'}): {info['timings']['dump']} c")

    report = parse(read_report(report_file))
    built = build_before(head, work / "before", report)
    info.update({"changed": len(report["changed"]), "addedObjects": len(report["added"]),
                 "vendorOnly": len(report["vendorOnly"]), **built})
    notes = [f"доработки относительно {name} {version} (конфигурация поставщика ИБ): объектов изменено "
             f"{len(report['changed'])}, новых {len(report['added'])}, "
             f"только у поставщика {len(report['vendorOnly'])}; "
             f"модулей поставщика восстановлено {len(built['restored'])}; "
             f"/CompareCfg {sec} c, выгрузка ИБ {info['timings']['dump']} c",
             "«было» по отчёту /CompareCfg: восстанавливаются только тексты модулей, свойства и формы объектов — "
             "как в ИБ"]
    notes += [f"модуль проверен целиком: {w}" for w in built["whole"]]
    if built["skipped"]:
        notes.append(f"не распознано в отчёте (путь не поддержан — модуль проверен как в ИБ, объект не убран из "
                     f"«было»): {len(built['skipped'])}, например "
                     + ", ".join(built["skipped"][:3]))
    return {"head": head, "base": work / "before", "info": info, "notes": notes}


def cleanup(run_dir):
    """Снять зеркало «было» прошлого или текущего прогона (только ссылки — цели в кэше выгрузки не трогаются)."""
    bsl_analyzer.remove_mirror(Path(run_dir) / "vendor" / "before")
