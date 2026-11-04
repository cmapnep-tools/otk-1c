"""Формат исходников: где в дереве лежат модули, описания объектов и форм, свойства конфигурации.

Единственное место, которое знает раскладку файлов. Остальной ОТК работает с нейтральным адресом модуля
Addr(collection, name, kind, sub) и спрашивает пути у формата. Пути — относительно корня исходников
(каталог с файлом конфигурации), через прямой слэш.

Адрес: collection — каталог коллекции («Catalogs», «CommonModules»; у модулей корня конфигурации — «»),
name — имя объекта, kind — вид модуля (SessionModule… у корня, Module у общих модулей и сервисов,
ObjectModule, ManagerModule, RecordSetModule, ValueManagerModule, CommandModule, FormModule),
sub — имя формы или команды объекта (у общих форм и команд — None).
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT_KINDS = ("SessionModule", "ManagedApplicationModule", "OrdinaryApplicationModule", "ExternalConnectionModule")
OBJECT_KINDS = ("Module", "ObjectModule", "ManagerModule", "RecordSetModule", "ValueManagerModule")
MODULE_FLAGS = ("Global", "Server", "ServerCall", "ClientManagedApplication", "ExternalConnection",
                "ClientOrdinaryApplication", "Privileged")
# защищённый модуль EDT: двоичный контейнер вместо текста (FF FF FF 7F);
# после чтения utf-8-sig/replace — U+FFFD x3 и DEL
_PROTECTED_BYTES = b"\xff\xff\xff\x7f"
_PROTECTED_TEXT = "���\x7f"


def decode(data):
    """Байты файла исходников -> текст, как его читает ОТК: utf-8-sig, нечитаемые байты — U+FFFD
    (переводы строк не меняются)."""
    return data.decode("utf-8-sig", "replace")


def read_text(path):
    """Текст файла исходников (см. decode) или None, если файла нет."""
    p = Path(path)
    return decode(p.read_bytes()) if p.is_file() else None


def find_ci(root, rel):
    """Путь root/rel без учёта регистра (имена в коде 1С регистр не различают, файловая система Linux — различает)
    или None, если такого файла нет."""
    p = Path(root) / rel
    if p.exists():
        return p
    p = Path(root)
    for part in Path(rel).parts:
        low = part.casefold()
        p = next((c for c in p.iterdir() if c.name.casefold() == low), None) if p.is_dir() else None
        if p is None:
            return None
    return p


def xml_children(text):
    """(корень, [(локальное имя, элемент)]) — прямые дочерние элементы корня XML; нечитаемый текст — (None, [])."""
    try:
        root = ET.fromstring((text or "").encode("utf-8"))
    except ET.ParseError:
        return None, []
    return root, [(e.tag.rpartition("}")[2], e) for e in root]


def xml_text(e, tag):
    """Текст первого прямого потомка e с локальным именем tag или None."""
    for c in e:
        if c.tag.rpartition("}")[2] == tag:
            return (c.text or "").strip()
    return None


@dataclass(frozen=True)
class Addr:
    collection: str
    name: str
    kind: str
    sub: str | None = None


@dataclass
class Properties:
    """Свойства конфигурации или расширения из файла конфигурации."""
    variant: str = "Russian"       # ScriptVariant
    run_mode: str | None = None            # DefaultRunMode: ManagedApplication | OrdinaryApplication | Auto | None
    extension: bool = False         # есть ConfigurationExtensionPurpose
    name: str | None = None                # имя конфигурации / расширения
    compat: str | None = None              # режим совместимости, в котором компилируется код (Version8_3_21) или None
    prefix: str = ""                # NamePrefix
    mobile_app: bool = False        # среди назначений использования — приложение для мобильной платформы
    modality: str | None = None            # ModalityUseMode: Use | UseWithWarnings | DontUse | None
    sync_calls: str | None = None          # SynchronousPlatformExtensionAndAddInCallUseMode: то же


class Designer:
    """Выгрузка Конфигуратора (designer-xml)."""
    format = "designer-xml"
    has_protected = False           # защищённые модули — Ext/*.bin, .bsl нет
    config_file = "Configuration.xml"
    dump_markers = ("Configuration.xml", "ConfigDumpInfo.xml")
    project_src = ""                # корень исходников внутри каталога проекта (раскладка для BSL LS)

    def parse(self, path):
        """Путь модуля -> Addr или None (не модуль или неизвестная раскладка)."""
        parts = path.split("/")
        if not path.endswith(".bsl"):
            return None
        if len(parts) == 2 and parts[0] == "Ext" and parts[1][:-4] in ROOT_KINDS:
            return Addr("", "", parts[1][:-4])
        if len(parts) < 4:
            return None
        coll, name, tail = parts[0], parts[1], parts[2:]
        if tail == ["Ext", "Form", "Module.bsl"] and coll == "CommonForms":
            return Addr(coll, name, "FormModule")
        if tail == ["Ext", "CommandModule.bsl"]:
            return Addr(coll, name, "CommandModule")
        if len(tail) == 2 and tail[0] == "Ext" and tail[1][:-4] in OBJECT_KINDS:
            return Addr(coll, name, tail[1][:-4])
        if len(tail) == 5 and tail[0] == "Forms" and tail[2:] == ["Ext", "Form", "Module.bsl"]:
            return Addr(coll, name, "FormModule", tail[1])
        if len(tail) == 4 and tail[0] == "Commands" and tail[2:] == ["Ext", "CommandModule.bsl"]:
            return Addr(coll, name, "CommandModule", tail[1])
        return None

    def module_path(self, a):
        """Addr -> путь модуля (обратное к parse)."""
        if not a.collection:
            return f"Ext/{a.kind}.bsl"
        root = f"{a.collection}/{a.name}"
        if a.kind == "FormModule":
            return f"{root}/Forms/{a.sub}/Ext/Form/Module.bsl" if a.sub else f"{root}/Ext/Form/Module.bsl"
        if a.kind == "CommandModule":
            return f"{root}/Commands/{a.sub}/Ext/CommandModule.bsl" if a.sub else f"{root}/Ext/CommandModule.bsl"
        return f"{root}/Ext/{a.kind}.bsl"

    def to_designer(self, path):
        """Путь файла исходников -> путь того же файла в выгрузке Конфигуратора (для Конфигуратора — он сам)."""
        return path

    def from_designer(self, path):
        """Путь файла выгрузки Конфигуратора -> путь файла исходников (для Конфигуратора — он сам)."""
        return path

    def object_file(self, collection, name):
        """Описание объекта метаданных (свойства, реквизиты, флаги общего модуля)."""
        return f"{collection}/{name}.xml"

    def object_names(self, root, collection):
        """Имена объектов коллекции по описаниям на диске, в порядке путей."""
        return [p.stem for p in sorted((Path(root) / collection).glob("*.xml"))]

    def form_files(self, collection, name, form=None):
        """(описание формы, содержимое формы — элементы, реквизиты, обработчики). form=None — общая форма."""
        root = f"{collection}/{name}" + (f"/Forms/{form}" if form else "")
        return f"{root}.xml", f"{root}/Ext/Form.xml"

    def form_module(self, content_path):
        """Файл содержимого формы -> путь модуля этой формы (None — это не содержимое формы)."""
        if not content_path.endswith("/Ext/Form.xml"):
            return None
        return content_path[:-len("Form.xml")] + "Form/Module.bsl"

    def owner_key(self, path):
        """Объект, которому принадлежит файл: «Catalogs/N» (модули, формы, описания объекта); файлы корня — сами."""
        parts = path.split("/")
        return "/".join(parts[:2]) if len(parts) > 2 else path

    def config_children(self, text):
        """Состав конфигурации по тексту файла конфигурации: [(тег типа, имя)] в порядке файла."""
        text = text or ""
        children = text[text.find("<ChildObjects>"):] if "<ChildObjects>" in text else ""
        return [(tag, name.strip()) for tag, name in re.findall(r"<(\w+)>([^<]+)</\1>", children)]

    def module_flags(self, text):
        """Флаги общего модуля по описанию: {Global, Server, …: bool}."""
        return {f: bool(re.search(rf"<{f}>true</{f}>", text or "")) for f in MODULE_FLAGS}

    def ordinary_form(self, desc_text, form=None):
        """Обычная ли форма по описанию формы (form_files()[0])."""
        return "<FormType>Ordinary</FormType>" in (desc_text or "")

    def protected(self, text):
        """Защищённый модуль (исходного текста нет)? В выгрузке Конфигуратора такие модули — Ext/*.bin, .bsl нет."""
        return False

    def form_attributes(self, form_text):
        """Имена реквизитов и параметров формы (как в файле)."""
        return re.findall(r'<(?:Attribute|Parameter) name="([^"]+)"', form_text or "")

    def form_handlers(self, form_text):
        """Имена процедур-обработчиков событий формы и элементов (с повторами)."""
        return re.findall(r'<Event name="[^"]+"[^>]*>\s*([^<\s]+)\s*</Event>', form_text or "")

    def is_root(self, root):
        return (Path(root) / self.config_file).is_file()

    def properties(self, text):
        """Свойства по тексту файла конфигурации (None — пустые свойства)."""
        text = text or ""
        def tag(t):
            return (m.group(1) if (m := re.search(rf"<{t}>([^<]*)</{t}>", text)) else None)
        ext = "<ConfigurationExtensionPurpose>" in text
        name = re.search(r"<Properties>.*?<Name>([^<]+)</Name>", text, re.S)
        compat = tag("ConfigurationExtensionCompatibilityMode" if ext else "CompatibilityMode")
        purposes = re.search(r"<UsePurposes>(.*?)</UsePurposes>", text, re.S)
        return Properties(variant=tag("ScriptVariant") or "Russian", run_mode=tag("DefaultRunMode") or None,
                          extension=ext,
                          name=name.group(1).strip() if name else None,
                          compat=compat if compat and compat != "DontUse" else None, prefix=tag("NamePrefix") or "",
                          mobile_app=bool(purposes and "MobilePlatformApplication" in purposes.group(1)),
                          modality=tag("ModalityUseMode"),
                          sync_calls=tag("SynchronousPlatformExtensionAndAddInCallUseMode"))

    def read_properties(self, root):
        p = Path(root) / self.config_file
        return self.properties(read_text(p))


class Edt:
    """Исходники 1С:EDT (каталог src проекта): модули рядом с описанием, без Ext; описание — .mdo, форма — Form.form."""
    format = "edt"
    has_protected = True            # защищённый модуль — .bsl с двоичным содержимым (protected)
    config_file = "Configuration/Configuration.mdo"
    # в EDT нет файла, который переписывается при каждой выгрузке: отпечаток кеша всегда по всем файлам
    # (index.dir_fingerprint при пустом dump_markers обходит все файлы, как при full=True)
    dump_markers = ()
    # BSL LS узнаёт исходники EDT только как проект: <каталог>/src/Configuration/Configuration.mdo; без src
    # метаданные не читаются молча (на типовой пропали CompilationDirectiveLost, WrongDataPathForFormElements…)
    project_src = "src"

    def parse(self, path):
        """Путь модуля -> Addr или None (не модуль, BaseForm адаптированной формы или неизвестная раскладка)."""
        parts = path.split("/")
        if not path.endswith(".bsl"):
            return None
        if len(parts) == 2 and parts[0] == "Configuration" and parts[1][:-4] in ROOT_KINDS:
            return Addr("", "", parts[1][:-4])
        if len(parts) < 3:
            return None
        coll, name, tail = parts[0], parts[1], parts[2:]
        if tail == ["Module.bsl"] and coll == "CommonForms":
            return Addr(coll, name, "FormModule")
        if tail == ["CommandModule.bsl"]:
            return Addr(coll, name, "CommandModule")
        if len(tail) == 1 and tail[0][:-4] in OBJECT_KINDS:
            return Addr(coll, name, tail[0][:-4])
        if len(tail) == 3 and tail[0] == "Forms" and tail[2] == "Module.bsl":
            return Addr(coll, name, "FormModule", tail[1])
        if len(tail) == 3 and tail[0] == "Commands" and tail[2] == "CommandModule.bsl":
            return Addr(coll, name, "CommandModule", tail[1])
        return None

    def module_path(self, a):
        """Addr -> путь модуля (обратное к parse)."""
        if not a.collection:
            return f"Configuration/{a.kind}.bsl"
        root = f"{a.collection}/{a.name}"
        if a.kind == "FormModule":
            return f"{root}/Forms/{a.sub}/Module.bsl" if a.sub else f"{root}/Module.bsl"
        if a.kind == "CommandModule":
            return f"{root}/Commands/{a.sub}/CommandModule.bsl" if a.sub else f"{root}/CommandModule.bsl"
        return f"{root}/{a.kind}.bsl"

    def to_designer(self, path):
        """Путь файла EDT -> путь того же файла в выгрузке Конфигуратора (в неё превращает проект экспорт 1cedtcli):
        модуль, Form.form (-> Form.xml), Configuration.mdo (-> Configuration.xml). None — однозначно не пересчитывается:
        .mdo объекта (в нём и то, что в выгрузке — отдельные файлы: свойства форм и макетов, предопределённые, состав
        плана обмена), BaseForm, роли, макеты…: нужна полная загрузка."""
        a = self.parse(path)
        if a is not None:
            return DESIGNER.module_path(a)
        if path == self.config_file:
            return DESIGNER.config_file
        module = self.form_module(path)
        a = self.parse(module) if module else None
        return DESIGNER.form_files(a.collection, a.name, a.sub)[1] if a else None

    def from_designer(self, path):
        """Путь файла выгрузки Конфигуратора -> путь файла EDT (обратное к to_designer; None — не пересчитывается):
        модуль, Ext/Form.xml (-> Form.form), Configuration.xml; описание объекта <Колл>/<Имя>.xml, его предопределённые
        и описания его форм и макетов (-> .mdo объекта: в EDT это всё в нём). Путь не относительный (не под корнем
        выгрузки) и корневые Ext/*.xml (интерфейс, рабочая область) — None."""
        path = path.replace("\\", "/")
        parts = path.split("/")
        if path.startswith("/") or ":" in parts[0]:
            return None
        a = DESIGNER.parse(path)
        if a is not None:
            return self.module_path(a)
        if parts[0] == "Ext":
            return None
        if path == DESIGNER.config_file:
            return self.config_file
        if path.endswith("/Ext/Form.xml"):
            return path[:-len("Ext/Form.xml")] + "Form.form"
        if len(parts) == 2 and parts[1].endswith(".xml"):
            return self.object_file(parts[0], parts[1][:-4])
        if len(parts) == 4 and parts[2:] == ["Ext", "Predefined.xml"] or \
                len(parts) == 4 and parts[2] in ("Forms", "Templates") and parts[3].endswith(".xml"):
            return self.object_file(parts[0], parts[1])
        return None

    def object_file(self, collection, name):
        """Описание объекта метаданных (свойства, реквизиты, флаги общего модуля)."""
        return f"{collection}/{name}/{name}.mdo"

    def object_names(self, root, collection):
        """Имена объектов коллекции по описаниям на диске, в порядке путей."""
        base = Path(root) / collection
        if not base.is_dir():
            return []
        return [d.name for d in sorted(base.iterdir()) if (d / f"{d.name}.mdo").is_file()]

    def form_files(self, collection, name, form=None):
        """(описание формы, содержимое формы). У формы объекта своего описания нет — описание владельца."""
        owner = self.object_file(collection, name)
        if form:
            return owner, f"{collection}/{name}/Forms/{form}/Form.form"
        return owner, f"{collection}/{name}/Form.form"

    def form_module(self, content_path):
        """Файл содержимого формы -> путь модуля этой формы (None — не содержимое формы, в т.ч. BaseForm)."""
        if not content_path.endswith("/Form.form") or "/BaseForm/" in content_path:
            return None
        return content_path[:-len("Form.form")] + "Module.bsl"

    def owner_key(self, path):
        """Объект, которому принадлежит файл: «Catalogs/N» (модули, формы, описания объекта); файлы корня — сами."""
        parts = path.split("/")
        return "/".join(parts[:2]) if len(parts) > 2 else path

    def config_children(self, text):
        """Состав конфигурации по тексту Configuration.mdo: прямые дочерние элементы корня с текстом «Тип.Имя».
        Свойства-ссылки (defaultLanguage, defaultReportForm, defaultRoles…) — не состав: у расширения без
        заимствованной формы они дали бы фантомный объект."""
        _, items = xml_children(text)
        out = []
        for tag, e in items:
            kind, dot, name = (e.text or "").strip().partition(".")
            if dot and name and not len(e) and not tag.startswith("default"):
                out.append((kind, name))
        return out

    def module_flags(self, text):
        """Флаги общего модуля по .mdo: прямые дочерние элементы корня (server и т.п.); нет элемента — false."""
        _, items = xml_children(text)
        have = {tag: (e.text or "").strip() for tag, e in items}
        return {f: have.get(f[0].lower() + f[1:]) == "true" for f in MODULE_FLAGS}

    def ordinary_form(self, desc_text, form=None):
        """Обычная ли форма по .mdo владельца: у формы объекта — formType элемента forms с этим именем,
        у общей формы (form=None) — formType корня."""
        root, items = xml_children(desc_text)
        if root is None:
            return False
        if not form:
            return any(tag == "formType" and (e.text or "").strip() == "Ordinary" for tag, e in items)
        return any(tag == "forms" and xml_text(e, "name") == form and xml_text(e, "formType") == "Ordinary"
                   for tag, e in items)

    def protected(self, text):
        """Защищённый модуль: .bsl — двоичный контейнер (начало FF FF FF 7F). text — как читает ОТК
        (utf-8-sig, replace) или байты."""
        if isinstance(text, bytes):
            return text.startswith(_PROTECTED_BYTES)
        return (text or "").startswith(_PROTECTED_TEXT)

    def form_attributes(self, form_text):
        """Имена реквизитов и параметров формы: прямые потомки корня Form.form."""
        _, items = xml_children(form_text)
        return [n for tag, e in items if tag in ("attributes", "parameters") and (n := xml_text(e, "name"))]

    def form_handlers(self, form_text):
        """Имена процедур-обработчиков: handlers/name у формы и элементов
        (action команд не берётся, как в Конфигураторе)."""
        root, _ = xml_children(form_text)
        if root is None:
            return []
        return [n for e in root.iter() if e.tag.rpartition("}")[2] == "handlers" and (n := xml_text(e, "name"))]

    def is_root(self, root):
        return (Path(root) / self.config_file).is_file()

    def properties(self, text):
        """Свойства по тексту Configuration.mdo — только прямые дочерние элементы корня (в блоке extension свои
        defaultRunMode и interfaceCompatibilityMode со значением Checked). None/нечитаемый текст — пустые свойства.
        Нет scriptVariant — English: значение по умолчанию EMF не сериализует (Russian EDT пишет явно)."""
        try:
            root = ET.fromstring((text or "").encode("utf-8"))
        except ET.ParseError:
            return Properties()
        child, purposes = {}, set()
        for e in root:
            child.setdefault(e.tag.rpartition("}")[2], (e.text or "").strip())
            if e.tag.rpartition("}")[2] == "usePurposes":
                purposes.add((e.text or "").strip())
        ext = "configurationExtensionPurpose" in child
        compat = child.get("configurationExtensionCompatibilityMode" if ext else "compatibilityMode")
        if compat and re.fullmatch(r"\d+(\.\d+)+", compat):
            compat = "Version" + compat.replace(".", "_")
        return Properties(variant=child.get("scriptVariant") or "English", run_mode=child.get("defaultRunMode") or None,
                          extension=ext, name=child.get("name") or None,
                          compat=compat if compat and compat != "DontUse" else None,
                          prefix=child.get("namePrefix") or "", mobile_app="MobileDevice" in purposes,
                          modality=child.get("modalityUseMode") or None,
                          sync_calls=child.get("synchronousPlatformExtensionAndAddInCallUseMode") or None)

    def read_properties(self, root):
        p = Path(root) / self.config_file
        return self.properties(read_text(p))


DESIGNER = Designer()
EDT = Edt()
Layout = Designer | Edt              # формат выгрузки: DESIGNER или EDT
FORMATS = {DESIGNER.format: DESIGNER, EDT.format: EDT}


def form_layout(form_text):
    """Формат по содержимому формы: Form.form EDT (пространство имён dt/form) или Form.xml Конфигуратора."""
    return EDT if "g5.1c.ru/v8/dt/form" in (form_text or "")[:2000] else DESIGNER


def get(fmt=None):
    """Формат по имени из плана (sourceFormat); None — формат Конфигуратора."""
    return FORMATS[fmt or DESIGNER.format]


def detect(root):
    """Формат дерева исходников root или None, если это не исходники конфигурации/расширения."""
    for f in FORMATS.values():
        if f.is_root(root):
            return f
    return None


def source_root(path):
    """Корень исходников по указанному каталогу: каталог проекта EDT
    (внутри src/Configuration/Configuration.mdo) -> его src."""
    p = Path(path)
    return p / "src" if (p / "src" / EDT.config_file).is_file() else p


def detect_read(read):
    """Формат по функции чтения read(rel) -> текст или None: первый, у которого есть файл конфигурации; нет — None."""
    for f in FORMATS.values():
        if read(f.config_file) is not None:
            return f
    return None
