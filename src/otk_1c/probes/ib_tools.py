"""Инструменты в копии ИБ: YAxUnit и тестовое расширение otk (спецификация ib-build).

Тестовое расширение генерируется под основную конфигурацию (язык, режим совместимости интерфейса).
Оба расширения подключаются без безопасного режима. Конфликт имён YAxUnit с экспортом глобальных
модулей конфигурации («уже определена (Имя)») снимается переименованием в ЮТ<Имя> внутри YAxUnit.
"""
import hashlib
import re
import shutil
import uuid
from pathlib import Path

from .. import lang
from ..errors import InfraError
from ..sources import layout
from .platform import platform_version

EXT = "otk"
MODULE = "otk_ТестыПроб"
YAX_NAME = "YAXUNIT"          # имя расширения внутри .cfe
YAX_LANGUAGE = "Русский"      # язык, который заимствует YAxUnit
_TEMPLATE = Path(__file__).parent.parent / "onec" / f"{MODULE}.bsl"
# Потолок формата файлов ОТК: 2.20 проверен на 8.3.27 и 8.5.1. Конфигуратор не грузит выгрузку из файлов
# разных версий, а версия выше платформы — «Неизвестная версия формата».
FORMAT_MAX = "2.20"
# Максимальная версия формата выгрузки платформы (ИТС «2.17.2. Версии формата выгрузки»: загрузка успешна, только
# если версия файлов меньше или равна версии платформы).
_PLATFORM_FORMATS = {(8, 3, 21): "2.14", (8, 3, 22): "2.15", (8, 3, 23): "2.16", (8, 3, 24): "2.17",
                     (8, 3, 25): "2.18", (8, 3, 26): "2.19", (8, 3, 27): "2.20", (8, 5, 1): "2.22"}
_NS = ('xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
       'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
       'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
       'xmlns:xen="http://v8.1c.ru/8.3/xcf/enums" '
       'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')
_CONTAINED = ["9cd510cd-abfc-11d4-9434-004095e12fc7", "9fcd25a0-4822-11d4-9414-008048da11f9",
              "e3687481-0a87-462c-a166-9f34594f9bba", "9de14907-ec23-4a07-96f0-85521cb6b53b",
              "51f2d5d8-ea4d-4064-8892-82951750031e", "e68182ea-4237-4383-967f-90c1e3370bc7",
              "fb282519-d103-4dd3-bc12-cb271d631dfc"]


def _ns(fmt):
    """Атрибуты MetaDataObject с версией формата файлов выгрузки."""
    return f'{_NS} version="{fmt}"'


_FORMAT_ATTR = re.compile(r'<MetaDataObject[^>]*\bversion="([^"]*)"')
_FORMAT_VALUE = re.compile(r"\d+\.\d+")


def _version_key(value):
    return tuple(int(x) for x in str(value).split("."))


def format_for_platform(version):
    """Максимальная версия формата выгрузки платформы version (8.3.27.2342 -> 2.20) или None: версия неизвестна
    или старее первой строки таблицы ИТС."""
    try:
        key = _version_key(version)[:3]
    except (TypeError, ValueError):
        return None
    known = [(k, v) for k, v in _PLATFORM_FORMATS.items() if k <= key]
    return max(known)[1] if known else None


def file_format(config_root):
    """Версия формата выгрузки Конфигуратора (атрибут version корня Configuration.xml) или None: исходники EDT,
    файла нет, версии формата в нём нет. Нечитаемый файл и неразбираемая версия — сбой инфраструктуры с
    названием файла: иначе формат молча не проверен, а расширение собрано в чужой версии."""
    if config_root is None or layout.detect(config_root) is layout.EDT:
        return None
    path = Path(config_root) / "Configuration.xml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise InfraError(f"не читается {path}: {e} — версию формата не определить") from e
    m = _FORMAT_ATTR.search(text)
    if not m:
        return None
    value = m.group(1)
    if not _FORMAT_VALUE.fullmatch(value):
        raise InfraError(f"{path}: версия формата файлов version=\"{value}\" не разбирается — ОТК сверяет "
                         "формат выгрузки с платформой по виду 2.16")
    return value


def generated_format(config_root, bin_dir=None):
    """Версия формата файлов, которые генерирует ОТК: у выгрузки — её же версия (иначе Конфигуратор не грузит
    файлы разных версий), у исходников EDT и у выгрузки без версии формата — по версии платформы; не выше потолка
    FORMAT_MAX."""
    edt = config_root is not None and layout.detect(config_root) is layout.EDT
    platform_fmt = format_for_platform(platform_version(bin_dir)) if bin_dir else None
    fmt = platform_fmt if edt else (file_format(config_root) or platform_fmt)
    return min(fmt or FORMAT_MAX, FORMAT_MAX, key=_version_key)


def require_format(config_root, bin_dir):
    """Ошибка до сборки копии ИБ, если выгрузка сделана платформой новее проверяющей: её файлы не загрузятся."""
    dump = file_format(config_root)
    platform = format_for_platform(platform_version(bin_dir)) if bin_dir else None
    if dump and platform and _version_key(dump) > _version_key(platform):
        raise InfraError(f"формат выгрузки {dump} новее платформы {platform_version(bin_dir)} (до {platform}): "
                         "выгрузку этой платформой не загрузить — проверьте платформой новее или возьмите выгрузку "
                         "этой платформы", code="format-unsupported")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def adopt_language(dump, old, props):
    """Выгрузка расширения: заимствованный язык old → основной язык конфигурации (имя, uuid, код)."""
    dump = Path(dump)
    src = dump / "Languages" / f"{old}.xml"
    text = src.read_text(encoding="utf-8-sig")
    text = re.sub(r"<Name>[^<]*</Name>", f"<Name>{props['language']}</Name>", text, count=1)
    text = re.sub(r"<ExtendedConfigurationObject>[^<]*</ExtendedConfigurationObject>",
                  f"<ExtendedConfigurationObject>{props['languageUuid']}</ExtendedConfigurationObject>", text, count=1)
    text = re.sub(r"<LanguageCode>[^<]*</LanguageCode>", f"<LanguageCode>{props['languageCode']}</LanguageCode>", text,
                  count=1)
    src.unlink()
    write(dump / "Languages" / f"{props['language']}.xml", text)
    for xml in dump.rglob("*.xml"):
        t = xml.read_text(encoding="utf-8-sig")
        new = t.replace(f"Language.{old}<", f"Language.{props['language']}<").replace(
            f"<Language>{old}</Language>", f"<Language>{props['language']}</Language>")
        if new != t:
            write(xml, new)


def base_properties(config_root, bin_dir=None):
    """Язык (uuid), режим совместимости интерфейса и версия формата файлов, которые генерирует ОТК, для основной
    конфигурации (выгрузка Конфигуратора или EDT; bin_dir — платформа, по ней формат у исходников EDT)."""
    if layout.detect(config_root) is layout.EDT:
        props = _edt_properties((Path(config_root) / layout.EDT.config_file).read_text(encoding="utf-8-sig"))
    else:
        cfg = (Path(config_root) / "Configuration.xml").read_text(encoding="utf-8-sig")
        lang_name = re.search(r"<DefaultLanguage>Language\.([^<]+)</DefaultLanguage>", cfg).group(1)
        lang_xml = (Path(config_root) / "Languages" / f"{lang_name}.xml").read_text(encoding="utf-8-sig")
        props = {"language": lang_name,
                 "languageUuid": re.search(r'<Language uuid="([^"]+)"', lang_xml).group(1),
                 "languageCode": re.search(r"<LanguageCode>([^<]+)</LanguageCode>", lang_xml).group(1),
                 "interface": re.search(r"<InterfaceCompatibilityMode>([^<]+)</InterfaceCompatibilityMode>",
                                        cfg).group(1)}
    props["format"] = generated_format(config_root, bin_dir)
    return props


def _edt_properties(text):
    """base_properties по Configuration.mdo: языки — элементы languages корня (uuid, name, languageCode);
    свойство со значением по умолчанию EDT не пишет — interfaceCompatibilityMode тогда Version8_2."""
    _, items = layout.xml_children(text)
    props = {tag: (e.text or "").strip() for tag, e in items if len(e) == 0}
    lang_name = props["defaultLanguage"].split(".", 1)[1]
    lang = next(e for tag, e in items if tag == "languages" and layout.xml_text(e, "name") == lang_name)
    return {"language": lang_name, "languageUuid": lang.get("uuid"), "languageCode": layout.xml_text(lang,
                                                                                                     "languageCode"),
            "interface": props.get("interfaceCompatibilityMode") or "Version8_2"}


_ORDER = ["Language", "Subsystem", "StyleItem", "Style", "CommonPicture", "SessionParameter", "Role", "CommonTemplate",
          "FilterCriterion", "CommonModule", "CommonAttribute", "ExchangePlan", "XDTOPackage", "WebService",
          "HTTPService", "WSReference", "EventSubscription", "ScheduledJob", "SettingsStorage", "FunctionalOption",
          "FunctionalOptionsParameter", "DefinedType", "CommonCommand", "CommandGroup", "Constant", "CommonForm",
          "Catalog", "Document", "Sequence", "DocumentJournal", "Enum", "Report", "DataProcessor",
          "InformationRegister", "AccumulationRegister", "ChartOfCharacteristicTypes", "ChartOfAccounts",
          "AccountingRegister", "ChartOfCalculationTypes", "CalculationRegister", "BusinessProcess", "Task"]
# ссылка на объект основной конфигурации через менеджер: Справочники.Имя / Catalogs.Имя
_REF = None


def referenced_objects(texts):
    """{(коллекция, имя)} объектов, на которые модули ссылаются через менеджеры (на любом языке)."""
    global _REF
    managers = lang.metadata_names()["managers"]
    if _REF is None:
        alt = "|".join(sorted(managers, key=len, reverse=True))
        _REF = re.compile(rf"(?<![\wА-Яа-яЁё.])({alt})\s*\.\s*([\wА-Яа-яЁё]+)", re.I)
    out = set()
    for text in texts:
        for m in _REF.finditer(text or ""):
            out.add((managers[m.group(1).lower()], m.group(2)))
    return out


def _generated_types(internal_info):
    return "".join(f'<xr:GeneratedType name="{t}" category="{c}"><xr:TypeId>{uuid.uuid4()}</xr:TypeId>'
                   f"<xr:ValueId>{uuid.uuid4()}</xr:ValueId></xr:GeneratedType>"
                   for t, c in re.findall(r'<xr:GeneratedType name="([^"]+)" category="([^"]+)"', internal_info))


def _adopted_fields(body, kinds="Attribute|Dimension|Resource|AccountingFlag|ExtDimensionAccountingFlag"):
    """Заимствованные реквизиты (поля) из текста ChildObjects — с типами."""
    return "".join(
        f'<{kind} uuid="{uuid.uuid4()}"><InternalInfo/><Properties><ObjectBelonging>Adopted</ObjectBelonging>'
        f"<Name>{field_name}</Name><Comment/><ExtendedConfigurationObject>{field_uuid}</ExtendedConfigurationObject>"
        f"{field_type.group(0) if field_type else ''}</Properties></{kind}>"
        for kind, field_uuid, fbody in re.findall(rf'<({kinds}) uuid="([^"]+)">(.*?)</\1>', body, re.S)
        for field_name in [re.search(r"<Name>([^<]+)</Name>", fbody).group(1)]
        for field_type in [re.search(r"<Type>.*?</Type>", fbody, re.S)])


def _adopted_tabular(text):
    """Заимствованные табличные части с реквизитами: модулю объекта нужны их имена (иначе «Переменная не
    определена (Товары)», по опыту)."""
    out = []
    for ts_uuid, body in re.findall(r'<TabularSection uuid="([^"]+)">(.*?)</TabularSection>', text, re.S):
        info = re.search(r"<InternalInfo>(.*?)</InternalInfo>", body, re.S)
        ts_name = re.search(r"<Name>([^<]+)</Name>", body).group(1)
        kids = re.search(r"<ChildObjects>(.*)</ChildObjects>", body, re.S)
        types = _generated_types(info.group(1) if info else "")
        out.append(f'<TabularSection uuid="{uuid.uuid4()}"><InternalInfo>{types}'
                   f"</InternalInfo><Properties><ObjectBelonging>Adopted</ObjectBelonging><Name>{ts_name}</Name>"
                   f"<Comment/><ExtendedConfigurationObject>{ts_uuid}</ExtendedConfigurationObject></Properties>"
                   f"<ChildObjects>{_adopted_fields(kids.group(1) if kids else '', 'Attribute')}</ChildObjects>"
                   "</TabularSection>")
    return "".join(out)


_COMMON_ATTRIBUTE_PROPS = ("Type", "AutoUse", "DataSeparation", "SeparatedDataUse")


def adopted_object(config_root, collection, name, lay=layout.DESIGNER, tabular=False, forms=(), fmt=None):
    """Описание заимствованного объекта основной конфигурации для расширения или None (нет такого объекта):
    свои uuid и сгенерированные типы, ExtendedConfigurationObject — uuid объекта; у перечисления — значения.
    tabular — с табличными частями (модуль объекта во временном расширении); forms — имена
    заимствованных форм объекта (<Form> в ChildObjects, после табличных частей); fmt — версия формата файлов."""
    path = layout.find_ci(config_root, lay.object_file(collection, name))
    if path is None:
        return None
    text = path.read_text(encoding="utf-8-sig")
    m = re.search(r'<(\w+) uuid="([^"]+)">', text.split("<MetaDataObject", 1)[-1])
    if not m:
        return None
    tag, main_uuid = m.group(1), m.group(2)
    real_name = re.search(r"<Properties>\s*<Name>([^<]+)</Name>", text)
    real_name = real_name.group(1) if real_name else name
    own = re.search(r"<InternalInfo>(.*?)</InternalInfo>", text, re.S)     # только типы самого объекта, не его ТЧ
    own = own.group(1) if own else ""
    # план обмена без своего ThisNode («свойство ThisNode типа InternalInfo отсутствует») не загружается
    types = f"<xr:ThisNode>{uuid.uuid4()}</xr:ThisNode>" if "<xr:ThisNode>" in own else ""
    types += _generated_types(own)
    # реквизиты и поля регистров верхнего уровня (без табличных частей) — с типами: без них расширенная проверка
    # не знает реквизитов объекта («возможно ошибочное свойство» и на верном имени, и на опечатке)
    top = re.sub(r"<TabularSection .*?</TabularSection>", "", text, flags=re.S)
    children = _adopted_fields(top) + (_adopted_tabular(text) if tabular else "")
    if tag == "Enum":
        children = "".join(f'<EnumValue uuid="{uuid.uuid4()}"><InternalInfo/><Properties>'
                           f"<ObjectBelonging>Adopted</ObjectBelonging><Name>{v}</Name><Comment/>"
                           f"<ExtendedConfigurationObject>{u}</ExtendedConfigurationObject></Properties></EnumValue>"
                           for u, v in re.findall(r'<EnumValue uuid="([^"]+)">\s*(?:<InternalInfo/>\s*)?<Properties>'
                                                  r'\s*<Name>([^<]+)</Name>', text))
    # ChildObjects — только у видов, где он есть: константа с ним не загружается («ожидаемое Constant»)
    children += "".join(f"<Form>{f}</Form>" for f in forms)
    child_objects = f"<ChildObjects>{children}</ChildObjects>" if "<ChildObjects" in text else ""
    # общий реквизит-разделитель без своих Type/AutoUse/DataSeparation объекты в расширении не видят: «Переменная
    # не определена (ОбластьДанных…)» в модуле объекта (проверено на отраслевой конфигурации); Content не нужен
    own_props = "".join(m.group(0) for t in _COMMON_ATTRIBUTE_PROPS
                        for m in [re.search(rf"<{t}>.*?</{t}>", text, re.S)] if m) if tag == "CommonAttribute" else ""
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject {_ns(fmt or FORMAT_MAX)}><{tag} '
           f'uuid="{uuid.uuid4()}">'
           f"<InternalInfo>{types}</InternalInfo><Properties><ObjectBelonging>Adopted</ObjectBelonging>"
           f"<Name>{real_name}</Name><Comment/><ExtendedConfigurationObject>{main_uuid}</ExtendedConfigurationObject>"
           f"{own_props}</Properties>{child_objects}</{tag}></MetaDataObject>")
    return {"collection": collection, "name": real_name, "tag": tag, "xml": xml}


def generate_test_extension(target, props, name=EXT, prefix="otk_", modules=None, objects=None):
    """Выгрузка расширения: язык + общие модули {имя: (флаги, текст)} + заимствованные объекты
    [{collection, name, tag, xml}] (adopted_object). Версия формата файлов — props["format"]."""
    fmt = props.get("format") or FORMAT_MAX
    shutil.rmtree(target, ignore_errors=True)
    target = Path(target)
    if modules is None:
        modules = {MODULE: ({"ClientManagedApplication": "true", "Server": "true",
                             "ClientOrdinaryApplication": "true"}, _TEMPLATE.read_text(encoding="utf-8-sig"))}
    contained = "".join(f"<xr:ContainedObject><xr:ClassId>{c}</xr:ClassId><xr:ObjectId>{uuid.uuid4()}</xr:ObjectId>"
                        "</xr:ContainedObject>" for c in _CONTAINED)
    children = "".join(f"<CommonModule>{m}</CommonModule>" for m in modules)
    # заимствованные объекты основной конфигурации — после общих модулей, в каноническом порядке видов
    objects = sorted(objects or [], key=lambda o: (_ORDER.index(o["tag"]) if o["tag"] in _ORDER else len(_ORDER),
                                                  o["name"]))
    children += "".join(f"<{o['tag']}>{o['name']}</{o['tag']}>" for o in objects)
    for o in objects:
        write(target / o["collection"] / f"{o['name']}.xml", o["xml"])
    write(target / "Configuration.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {_ns(fmt)}><Configuration uuid="{uuid.uuid4()}"><InternalInfo>{contained}</InternalInfo><Properties>
<ObjectBelonging>Adopted</ObjectBelonging><Name>{name}</Name><Synonym/><Comment/>
<ConfigurationExtensionPurpose>AddOn</ConfigurationExtensionPurpose>
<KeepMappingToExtendedConfigurationObjectsByIDs>true</KeepMappingToExtendedConfigurationObjectsByIDs>
<NamePrefix>{prefix}</NamePrefix><ConfigurationExtensionCompatibilityMode>Version8_3_21</ConfigurationExtensionCompatibilityMode>
<DefaultRunMode>ManagedApplication</DefaultRunMode>
<UsePurposes><v8:Value xsi:type="app:ApplicationUsePurpose">PlatformApplication</v8:Value></UsePurposes>
<ScriptVariant>Russian</ScriptVariant><DefaultRoles/><Vendor>otk</Vendor><Version>1.0</Version>
<DefaultLanguage>Language.{props['language']}</DefaultLanguage><BriefInformation/><DetailedInformation/><Copyright/>
<VendorInformationAddress/><ConfigurationInformationAddress/><InterfaceCompatibilityMode>{props['interface']}</InterfaceCompatibilityMode>
</Properties><ChildObjects><Language>{props['language']}</Language>{children}</ChildObjects>
</Configuration></MetaDataObject>""")
    write(target / "Languages" / f"{props['language']}.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {_ns(fmt)}><Language uuid="{uuid.uuid4()}"><InternalInfo/>\
<Properties><ObjectBelonging>Adopted</ObjectBelonging>
<Name>{props['language']}</Name><Comment/><ExtendedConfigurationObject>{props['languageUuid']}</ExtendedConfigurationObject>
<LanguageCode>{props['languageCode']}</LanguageCode></Properties></Language></MetaDataObject>""")
    for mod_name, (flags, text) in modules.items():
        write_common_module(target, mod_name, flags, text, fmt)
    return target


def write_common_module(target, mod_name, flags, text, fmt=None):
    """Общий модуль в выгрузке: описание с флагами {"Server": "true", …} и текст модуля; fmt — версия формата
    файлов (по умолчанию потолок ОТК или формат Configuration.xml этой выгрузки)."""
    target = Path(target)
    fmt = fmt or file_format(target) or FORMAT_MAX
    head = flags.pop("__head__", f"<Name>{mod_name}</Name><Synonym/><Comment/>")
    all_flags = {f: flags.get(f, "false") for f in ("Global", "ClientManagedApplication", "Server",
                                                   "ExternalConnection", "ClientOrdinaryApplication",
                                                   "ServerCall", "Privileged")}
    props_xml = "".join(f"<{f}>{v}</{f}>" for f, v in all_flags.items())
    write(target / "CommonModules" / f"{mod_name}.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {_ns(fmt)}><CommonModule uuid="{uuid.uuid4()}"><Properties>{head}
{props_xml}<ReturnValuesReuse>DontUse</ReturnValuesReuse></Properties></CommonModule></MetaDataObject>""")
    write(target / "CommonModules" / mod_name / "Ext" / "Module.bsl", text)


def tools_version(yaxunit_cfe, props):
    h = hashlib.sha1(Path(yaxunit_cfe).read_bytes()[:1 << 20], usedforsecurity=False)
    h.update(_TEMPLATE.read_bytes())
    h.update(repr(sorted(props.items())).encode())
    return h.hexdigest()[:16]


def unsafe(platform, name):
    rc, _, out = platform.extension_unsafe(name)
    if rc:
        raise InfraError(f"снятие безопасного режима с {name}: {out[-300:]}", code="platform-failed")


def install(platform, run_dir, yaxunit_cfe, config_root, renames=()):
    """Установить YAxUnit (с переименованиями) и тестовое расширение. Возвращает описание."""
    run_dir = Path(run_dir)
    props = base_properties(config_root, getattr(platform, "bin", None))
    rc, _, out = platform.load_extension("YAxUnit", yaxunit_cfe, label="yax-load")
    if rc:
        raise InfraError(f"загрузка YAxUnit: {out[-300:]}", code="platform-failed")
    # YAxUnit заимствует язык «Русский»; в конфигурации с другим основным языком (английская БСП — только English)
    # расширение не применяется («Не найден объект Language.Русский») — заимствование переводится на её язык
    foreign_language = props["language"] != YAX_LANGUAGE
    if renames or foreign_language:
        dump = run_dir / "yaxunit-dump"
        shutil.rmtree(dump, ignore_errors=True)
        rc, _, out = platform.dump_files(dump, extension=YAX_NAME, label="yax-dump")
        if rc:
            raise InfraError(f"выгрузка YAxUnit: {out[-300:]}", code="platform-failed")
        for bsl in dump.rglob("*.bsl"):
            text = bsl.read_text(encoding="utf-8-sig")
            new = text
            for name in renames:
                new = re.sub(rf"(?<![\wА-Яа-яЁё]){name}(?![\wА-Яа-яЁё])", "ЮТ" + name, new)
            if new != text:
                bsl.write_bytes(b"\xef\xbb\xbf" + new.encode("utf-8"))
        if foreign_language and (dump / "Languages" / f"{YAX_LANGUAGE}.xml").exists():
            adopt_language(dump, YAX_LANGUAGE, props)
        rc, _, out = platform.load_dir(dump, extension=YAX_NAME, label="yax-patch")
        if rc:
            raise InfraError(f"загрузка YAxUnit с переименованиями: {out[-300:]}", code="platform-failed")
    rc, _, out = platform.update_db(extension=YAX_NAME, label="yax-update")
    if rc:
        raise InfraError(f"применение YAxUnit: {out[-300:]}", code="platform-failed")
    ext_dump = generate_test_extension(run_dir / "otk-ext", props)
    rc, _, out = platform.load_dir(ext_dump, extension=EXT, label="ext-load")
    if rc:
        raise InfraError(f"загрузка тестового расширения: {out[-300:]}", code="platform-failed")
    rc, _, out = platform.update_db(extension=EXT, label="ext-update")
    if rc:
        raise InfraError(f"применение тестового расширения: {out[-300:]}", code="platform-failed")
    unsafe(platform, YAX_NAME)
    unsafe(platform, EXT)
    return {"version": tools_version(yaxunit_cfe, props), "renames": list(renames)}


CONFLICT = re.compile(r"\{YAXUNIT [^}]*\}:\s*Процедура или функция с указанным именем уже определена \(([^)]+)\)")
