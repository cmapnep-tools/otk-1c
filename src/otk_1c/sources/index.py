"""Индекс метаданных выгрузки Конфигуратора для статических проверок (без базы).

Объекты по типам (реквизиты, табличные части, формы, команды, значения перечислений,
предопределённые элементы), экспортные методы общих модулей и модулей менеджеров с арностью,
ссылки на обработчики (подписки, регламентные задания). Для расширения — объединение с индексом
основной конфигурации (`parent`). Кеш — JSON по ключу: хеш дерева выгрузки в git, для каталогов вне
git — отпечаток каталога (dir_fingerprint).
"""
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import bsl, gitdiff, layout

# тег в Configuration.xml -> каталог выгрузки
COLLECTIONS = {
    "Catalog": "Catalogs", "Document": "Documents", "Enum": "Enums", "Report": "Reports",
    "DataProcessor": "DataProcessors", "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters", "AccountingRegister": "AccountingRegisters",
    "CalculationRegister": "CalculationRegisters", "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "ChartOfAccounts": "ChartsOfAccounts", "ChartOfCalculationTypes": "ChartsOfCalculationTypes",
    "BusinessProcess": "BusinessProcesses", "Task": "Tasks", "ExchangePlan": "ExchangePlans",
    "Constant": "Constants", "DocumentJournal": "DocumentJournals", "FilterCriterion": "FilterCriteria",
    "SettingsStorage": "SettingsStorages", "Sequence": "Sequences", "CommonModule": "CommonModules",
    "CommonForm": "CommonForms", "CommonCommand": "CommonCommands", "HTTPService": "HTTPServices",
    "WebService": "WebServices", "EventSubscription": "EventSubscriptions", "ScheduledJob": "ScheduledJobs",
}
_FIELD_TAGS = {"Attribute", "Dimension", "Resource", "AddressingAttribute", "AccountingFlag",
               "ExtDimensionAccountingFlag"}
INDEX_VERSION = 4


def _local(tag):
    return tag.split("}", 1)[-1]


def exports(text):
    """Экспортные методы модуля по заголовкам: {имя_в_нижнем_регистре: [имя, мин_арг, макс_арг]}.
    Быстрый разбор без лексера: строки-комментарии и комментарии внутри списка параметров пропускаются."""
    result = {}
    export = bsl.export_re()
    for m in bsl.header_re().finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:m.start()].lstrip().startswith("//"):
            continue
        depth, i, in_str, chars = 1, m.end(), False, []
        while i < len(text) and depth:
            ch = text[i]
            if not in_str and text.startswith("//", i):     # «Склад, //Если склады в ТЧ, то …» (ERP)
                eol = text.find("\n", i)
                i = len(text) if eol < 0 else eol
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                depth += {"(": 1, ")": -1}.get(ch, 0)
            chars.append(" " if in_str and ch != '"' else ch)
            i += 1
        params_text = "".join(chars[:-1])
        if not export.match(text[i:i + 200].split("\n", 1)[0]):
            continue
        params = [p for p in (x.strip() for x in _split(params_text)) if p]
        required = sum(1 for p in params if "=" not in p)
        result[m.group("name").lower()] = [m.group("name"), required, len(params)]
    return result


_VAR_EXPORT = re.compile(r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s+(?:Экспорт|Export)\b", re.I)


def global_vars(root, lay):
    """Экспортные переменные модулей приложения и внешнего соединения (`Перем Х Экспорт`): глобальные имена
    клиентского кода и внешнего соединения. Имена в нижнем регистре, по возрастанию."""
    names = set()
    for kind in layout.ROOT_KINDS:
        text = _read(root, lay.module_path(layout.Addr("", "", kind)))
        if not text or lay.protected(text):
            continue
        for decl in re.findall(r"(?im)^[ \t]*(?:Перем|Var)[ \t]+([^;]+);", bsl.parse(text).sanitized):
            names |= {m.group(1).lower() for m in _VAR_EXPORT.finditer(decl)}
    return sorted(names)


def _split(text):
    parts, depth, cur = [], 0, []
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _read(root, rel):
    p = root / rel
    return layout.read_text(p)


def _object(root, collection, name, lay=layout.DESIGNER):
    text = _read(root, lay.object_file(collection, name))
    entry = {"fields": [], "tabular": {}, "forms": [], "commands": [], "templates": [], "values": [],
             "predefined": [], "managerExports": {}}
    if text:
        try:
            obj = list(ET.fromstring(text))[0]
        except (ET.ParseError, IndexError):
            obj = None
        if obj is not None:
            props = obj.find("{*}Properties")
            if props is not None:
                for key in ("Handler", "MethodName", "Global", "Server", "ServerCall",
                            "ClientManagedApplication", "ExternalConnection", "ClientOrdinaryApplication"):
                    el = props.find(f"{{*}}{key}")
                    if el is not None and el.text:
                        entry.setdefault("props", {})[key] = el.text.strip()
            children = obj.find("{*}ChildObjects")
            for ch in (list(children) if children is not None else []):
                tag = _local(ch.tag)
                name_el = ch.find("{*}Properties/{*}Name")
                child_name = name_el.text if name_el is not None else (ch.text or "").strip()
                if tag in _FIELD_TAGS:
                    entry["fields"].append(child_name)
                elif tag == "TabularSection":
                    sub = ch.find("{*}ChildObjects")
                    entry["tabular"][child_name] = [
                        a.find("{*}Properties/{*}Name").text for a in (list(sub) if sub is not None else [])
                        if a.find("{*}Properties/{*}Name") is not None]
                elif tag == "EnumValue":
                    entry["values"].append(child_name)
                elif tag == "Form":
                    entry["forms"].append(child_name)
                elif tag == "Command":
                    entry["commands"].append(child_name)
                elif tag == "Template":
                    entry["templates"].append(child_name)
    predefined = _read(root, f"{collection}/{name}/Ext/Predefined.xml")
    if predefined:
        try:
            pr = ET.fromstring(predefined)
            entry["predefined"] = [el.text for el in pr.iter() if _local(el.tag) == "Name" and el.text]
        except ET.ParseError:
            pass
    manager = _read(root, lay.module_path(layout.Addr(collection, name, "ManagerModule")))
    if manager:
        entry["managerExports"] = exports(manager)
    return entry


_EDT_FIELDS = {"attributes", "dimensions", "resources", "addressingAttributes", "accountingFlags",
               "extDimensionAccountingFlags"}
_EDT_PROPS = {"handler": "Handler", "methodName": "MethodName", "global": "Global", "server": "Server",
              "serverCall": "ServerCall", "clientManagedApplication": "ClientManagedApplication",
              "externalConnection": "ExternalConnection", "clientOrdinaryApplication": "ClientOrdinaryApplication"}


def _name(e):
    """Текст дочернего <name> элемента EDT или None."""
    for c in e:
        if _local(c.tag) == "name":
            return (c.text or "").strip()
    return None


def _predefined_edt(items):
    """Имена предопределённых из <predefined><items>: элемент, затем вложенные <content> (любая глубина)."""
    out = []
    for e in items:
        name = _name(e)
        if name:
            out.append(name)
        out.extend(_predefined_edt([c for c in e if _local(c.tag) == "content"]))
    return out


def _object_edt(root, collection, name, lay=layout.EDT):
    """Тот же entry, что _object, по .mdo формата EDT: элементы верхнего уровня, имя — дочерний <name>."""
    text = _read(root, lay.object_file(collection, name))
    entry = {"fields": [], "tabular": {}, "forms": [], "commands": [], "templates": [], "values": [],
             "predefined": [], "managerExports": {}}
    obj = None
    if text:
        try:
            obj = ET.fromstring(text)
        except ET.ParseError:
            obj = None
    for ch in (list(obj) if obj is not None else []):
        tag = _local(ch.tag)
        if tag in _EDT_PROPS:
            if ch.text and ch.text.strip():
                entry.setdefault("props", {})[_EDT_PROPS[tag]] = ch.text.strip()
        elif tag in _EDT_FIELDS:
            n = _name(ch)
            if n is not None:
                entry["fields"].append(n)
        elif tag == "tabularSections":
            n = _name(ch)
            if n is not None:
                names = (_name(c) for c in ch if _local(c.tag) == "attributes")
                entry["tabular"][n] = [a for a in names if a is not None]
        elif tag in ("enumValues", "forms", "commands", "templates"):
            n = _name(ch)
            if n is not None:
                entry[{"enumValues": "values"}.get(tag, tag)].append(n)
        elif tag == "predefined":
            entry["predefined"] = _predefined_edt([c for c in ch if _local(c.tag) == "items"])
    manager = _read(root, lay.module_path(layout.Addr(collection, name, "ManagerModule")))
    if manager and not lay.protected(manager):
        entry["managerExports"] = exports(manager)
    return entry


def build(source_root, lay=None):
    """Индекс по каталогу исходников на диске; формат (Конфигуратор или EDT) определяется по файлу конфигурации."""
    root = Path(source_root)
    lay = lay or layout.detect(root) or layout.DESIGNER
    cfg = _read(root, lay.config_file) or ""
    index = {"version": INDEX_VERSION, "objects": {}, "commonModules": {}, "globalExports": {},
             "globalVars": global_vars(root, lay), "handlers": []}
    for tag, name in lay.config_children(cfg):
        collection = COLLECTIONS.get(tag)
        if not collection:
            continue
        if collection == "CommonModules":
            text = _read(root, lay.object_file(collection, name)) or ""
            flags = lay.module_flags(text)
            body = _read(root, lay.module_path(layout.Addr(collection, name, "Module")))
            if lay.protected(body):
                body = None             # защищённый модуль EDT: исходного текста нет (в выгрузке — Module.bin)
            exp = exports(body or "")
            index["commonModules"][name.lower()] = {"name": name, "flags": flags, "exports": exp,
                                                    "source": body is not None}
            if flags["Global"]:
                index["globalExports"].update(exp)
            continue
        entry = (_object_edt if lay.format == layout.EDT.format else _object)(root, collection, name, lay)
        props = entry.pop("props", {})
        if collection in ("EventSubscriptions", "ScheduledJobs"):
            ref = props.get("Handler") or props.get("MethodName")
            if ref:
                index["handlers"].append({"kind": collection, "object": name, "ref": ref})
            continue
        index["objects"].setdefault(collection, {})[name.lower()] = {"name": name, **entry}
    return index


def merge(child, parent):
    """Индекс расширения поверх индекса основной конфигурации."""
    out = json.loads(json.dumps(parent))
    for coll, objs in child["objects"].items():
        target = out["objects"].setdefault(coll, {})
        for key, obj in objs.items():
            if key in target:
                base = target[key]
                for field in ("fields", "forms", "commands", "templates", "values", "predefined"):
                    base[field] = sorted(set(base[field]) | set(obj[field]))
                for ts, attrs in obj["tabular"].items():
                    base["tabular"][ts] = sorted(set(base["tabular"].get(ts, [])) | set(attrs))
                base["managerExports"].update(obj["managerExports"])
            else:
                target[key] = obj
    for key, mod in child["commonModules"].items():
        if key in out["commonModules"]:
            out["commonModules"][key]["exports"].update(mod["exports"])
        else:
            out["commonModules"][key] = mod
    out["globalExports"].update(child["globalExports"])
    out["globalVars"] = sorted(set(out.get("globalVars", [])) | set(child.get("globalVars", [])))
    out["handlers"].extend(child["handlers"])
    return out


def cache_key(repo, source_dir):
    """Хеш дерева выгрузки в HEAD, если рабочее дерево по ней чистое; иначе None (без кеша)."""
    try:
        dirty = gitdiff.git(repo, "status", "--porcelain", "--", source_dir).strip()
        if dirty:
            return None
        return gitdiff.git(repo, "rev-parse", f"HEAD:{source_dir}").strip()
    except gitdiff.GitError:
        return None


def load(repo, source_dir, cache_dir=None, parent=None):
    """Индекс выгрузки репозитория (рабочее дерево), с кешем. parent — каталог основной конфигурации."""
    repo = Path(repo)
    key = cache_key(repo, source_dir)
    cache = Path(cache_dir) / f"index-{key}-v{INDEX_VERSION}.json" if cache_dir and key else None
    if cache and cache.exists():
        index = json.loads(cache.read_text(encoding="utf-8"))
    else:
        index = build(repo / source_dir)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    if parent:
        parent_index = build(Path(parent)) if not cache_dir else cached_dir(Path(parent), Path(cache_dir))
        index = merge(index, parent_index)
    return index


def dir_fingerprint(path, full, lay=layout.DESIGNER):
    """Отпечаток каталога выгрузки для ключа кеша. full=False (основная конфигурация, меняется редко) —
    размер и время файлов-маркеров выгрузки (lay.dump_markers: Конфигуратор переписывает их при любой выгрузке);
    full=True (проверяемая выгрузка, gate --dir) — размер и время всех файлов (os.scandir берёт их из листинга
    каталога: ERP, 122 тыс. файлов, — ~14 с против ~100 с построения индекса). Скрытые каталоги (.git и т.п.)
    не учитываются."""
    h = hashlib.sha1(usedforsecurity=False)

    def walk(d):
        with os.scandir(d) as it:
            for e in sorted(it, key=lambda e: e.name):
                if e.name.startswith("."):
                    continue
                if e.is_dir(follow_symlinks=False):
                    walk(e.path)
                else:
                    st = e.stat(follow_symlinks=False)
                    h.update(f"{e.path}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))

    if full or not lay.dump_markers:      # у формата без маркеров выгрузки (EDT) отпечаток всегда полный
        walk(path)
    else:
        for name in lay.dump_markers:
            f = Path(path) / name
            if f.exists():
                st = f.stat()
                h.update(f"{name}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def cached_dir(path, cache_dir, full=False):
    """Индекс каталога вне git с кешем: основная конфигурация (full=False) или проверяемая выгрузка без
    git (full=True), см. dir_fingerprint. Ключ — стабильный хеш пути (hash() строк меняется в каждом
    процессе Python) и отпечаток; прежние записи того же каталога удаляются — индексы ERP не копятся."""
    path = Path(path)
    lay = layout.detect(path)
    if lay is None:
        raise FileNotFoundError(path / layout.DESIGNER.config_file)
    cache_dir = Path(cache_dir)
    key = hashlib.sha1(str(path.resolve()).lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    prefix = f"index-dir-{key}-"
    cache = cache_dir / f"{prefix}{dir_fingerprint(path, full, lay)}-v{INDEX_VERSION}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    index = build(path, lay)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob(f"{prefix}*.json"):
        old.unlink(missing_ok=True)
    cache.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return index
