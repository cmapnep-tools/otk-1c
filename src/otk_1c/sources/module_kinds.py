"""Виды модулей, владельцы, флаги и контексты компиляции; раскладка файлов — layout (формат исходников).

Пути — относительно корня исходников (каталог с файлом конфигурации), через прямой слэш.
Контексты: Server, ThinClient, OrdinaryClient, ExternalConnection.
"""
import re
from dataclasses import dataclass, field

from .. import lang
from . import layout

COMMON_MODULE_FLAGS = ["Global", "ClientManagedApplication", "Server", "ExternalConnection",
                       "ClientOrdinaryApplication", "ServerCall", "Privileged"]

# модуль корня конфигурации -> (контексты, стратегия пробы)
_ROOT_MODULES = {
    "SessionModule": (["Server"], "session-start"),
    "ManagedApplicationModule": (["ThinClient"], "session-start"),
    "OrdinaryApplicationModule": (["OrdinaryClient"], "session-start"),
    "ExternalConnectionModule": (["ExternalConnection"], "session-start"),
}


@dataclass
class ModuleInfo:
    path: str
    kind: str
    owner: str
    contexts: list
    probe: dict
    flags: dict = field(default_factory=dict)
    xml: str | None = None                 # описание объекта/модуля (для флагов, загрузки формы и т.п.)
    companions: list = field(default_factory=list)   # файлы, которые грузятся вместе с модулем


def is_module(path):
    return path.endswith(".bsl")


def common_module_flags(xml_text):
    flags = {}
    for f in COMMON_MODULE_FLAGS:
        n = _flag_tags(f)
        m = re.search(rf"<{n}>(\w+)</{n}>", xml_text or "")
        flags[f] = bool(m and m.group(1) == "true")
    return flags


def _flag_tags(f):
    """Шаблон тега флага: Server (Конфигуратор) и server (EDT)."""
    return f"[{f[0]}{f[0].lower()}]{f[1:]}"


def _adopted_without_flags(xml_text):
    """Описание заимствованного объекта, в котором нет ни одного флага (EDT: в расширении флаги не хранятся)."""
    text = xml_text or ""
    return bool(re.search(r"<[Oo]bjectBelonging>Adopted<", text)
                and not any(re.search(f"<{_flag_tags(f)}>", text) for f in COMMON_MODULE_FLAGS))


def describe(path, read, variant="Russian", ordinary_application=False, lay=layout.DESIGNER,
             read_parent=None, parent_lay=None):
    """Описание модуля по пути. read(rel_path) -> текст файла исходников или None. Имена владельцев и код проб —
    канонические (русские) при любом варианте языка: variant только проверяется на поддержку. lay — формат
    исходников (layout): пути описаний объекта и формы берутся у него. read_parent(rel) -> описание в основной
    конфигурации (формат parent_lay, по умолчанию lay): флаги заимствованного общего модуля — из неё."""
    lang.dictionary(variant)
    words = lang.dictionary(lang.CANONICAL)
    types = words["metadataTypes"]
    w = words["words"]
    a = lay.parse(path)
    unknown = ModuleInfo(path, "Unknown", path, [], {"strategy": "none", "reason": "неизвестный вид модуля"})
    if a is None:
        return unknown

    if not a.collection:
        kind = a.kind
        contexts, strategy = _ROOT_MODULES[kind]
        return ModuleInfo(path, kind, kind, list(contexts), {"strategy": strategy})

    collection, name = a.collection, a.name
    single = types.get(collection, {}).get("single", collection)

    if collection == "CommonModules" and a.kind == "Module":
        xml_path = lay.object_file(collection, name)
        text = read(xml_path)
        if _adopted_without_flags(text):
            ptext = read_parent((parent_lay or lay).object_file(collection, name)) if read_parent else None
            if ptext is None:
                return ModuleInfo(path, "CommonModule", f"{single}.{name}", [],
                                  {"strategy": "none", "reason": "заимствованный общий модуль: флаги — в основной "
                                                                 "конфигурации (нужен --parent)"},
                                  common_module_flags(None), xml_path)
            text = ptext
        flags = common_module_flags(text)
        contexts = []
        if flags["Server"]:
            contexts.append("Server")
        if flags["ClientManagedApplication"]:
            contexts.append("ThinClient")
        if flags["ClientOrdinaryApplication"] and ordinary_application:
            # флаг есть у многих общих модулей управляемых конфигураций; контекст нужен, только если
            # конфигурация действительно работает в обычном приложении (collect: DefaultRunMode или настройка)
            contexts.append("OrdinaryClient")
        if flags["ExternalConnection"]:
            contexts.append("ExternalConnection")
        if flags["Global"]:
            probe = {"strategy": "session-start"}
        else:
            probe = {"strategy": "missing-method", "target": name}
        return ModuleInfo(path, "CommonModule", f"{single}.{name}", contexts, probe, flags, xml_path)

    if collection == "CommonForms" and a.kind == "FormModule" and not a.sub:
        owner = f"{single}.{name}"
        xml, content = lay.form_files(collection, name)
        return ModuleInfo(path, "FormModule", owner, ["ThinClient", "Server"],
                          {"strategy": "get-form", "target": owner}, xml=xml, companions=[content])

    if collection == "CommonCommands" and a.kind == "CommandModule" and not a.sub:
        return ModuleInfo(path, "CommandModule", f"{single}.{name}", ["ThinClient"],
                          {"strategy": "none", "reason": "модуль команды не пробуется без исполнения команды"})

    if collection in ("HTTPServices", "WebServices") and a.kind == "Module":
        kind = "HTTPService" if collection == "HTTPServices" else "WebService"
        return ModuleInfo(path, kind, f"{single}.{name}", ["Server"],
                          {"strategy": "none", "reason": "модуль сервиса не пробуется без публикации"})

    owner = f"{single}.{name}"
    info = types.get(collection, {})
    manager = info.get("manager")
    object_contexts = ["Server", "ExternalConnection"] + (["OrdinaryClient"] if ordinary_application else [])

    if a.kind == "FormModule" and a.sub:
        form_owner = f"{owner}.{w['form']}.{a.sub}"
        xml, content = lay.form_files(collection, name, a.sub)
        return ModuleInfo(path, "FormModule", form_owner, ["ThinClient", "Server"],
                          {"strategy": "get-form", "target": form_owner}, xml=xml, companions=[content])

    if a.kind == "CommandModule" and a.sub:
        return ModuleInfo(path, "CommandModule", f"{owner}.{w['command']}.{a.sub}", ["ThinClient"],
                          {"strategy": "none", "reason": "модуль команды не пробуется без исполнения команды"})

    xml = lay.object_file(collection, name)
    if a.kind == "ObjectModule":
        create = info.get("create")
        probe = ({"strategy": "create-object", "target": f"{manager}.{name}", "method": create}
                 if manager and create else {"strategy": "none", "reason": "нет конструктора объекта"})
        return ModuleInfo(path, "ObjectModule", owner, object_contexts, probe, xml=xml)
    if a.kind == "ManagerModule":
        probe = ({"strategy": "missing-method", "target": f"{manager}.{name}"} if manager
                 else {"strategy": "none", "reason": "нет менеджера"})
        return ModuleInfo(path, "ManagerModule", owner, object_contexts, probe, xml=xml)
    if a.kind == "RecordSetModule":
        probe = ({"strategy": "create-object", "target": f"{manager}.{name}", "method": info["recordSet"]}
                 if manager and info.get("recordSet") else {"strategy": "none", "reason": "нет набора записей"})
        return ModuleInfo(path, "RecordSetModule", owner, object_contexts, probe, xml=xml)
    if a.kind == "ValueManagerModule":
        probe = ({"strategy": "create-object", "target": f"{manager}.{name}", "method": info["valueManager"]}
                 if manager and info.get("valueManager") else {"strategy": "none", "reason": "нет менеджера значения"})
        return ModuleInfo(path, "ValueManagerModule", owner, object_contexts, probe, xml=xml)

    return unknown
