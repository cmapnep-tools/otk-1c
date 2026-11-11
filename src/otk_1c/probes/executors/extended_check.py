"""Расширенная проверка Конфигуратора для изменённых модулей (design.md §14, спайк O7).

Проект-расширение (project.extension): модуль расширения во временное расширение не заимствуется (вызовы
его собственных методов не разрешаются), поэтому проверяется само расширение — /CheckModules всех
режимов с -ExtendedModulesCheck -Extension <имя> (run_extension; на небольшом расширении — секунды).

Модули заимствуются во временное расширение otk_check под своими именами (с ними — объекты основной
конфигурации, на которые они ссылаются через менеджеры: без заимствования расширенная проверка в расширении
считает их «возможно ошибочными свойствами»); методы → перехватчики
&Вместо("Имя") с именем kc_Имя (иначе «уже определена»); /CheckModules … -ExtendedModulesCheck
-Extension otk_check без применения к БД; расширение удаляется. Проверка выполняется для
редакций «до» и «после»; в результат попадают только новые замечания.

В то же расширение — модули управляемых форм (и общих) и модули объектов, менеджеров, наборов записей.
Описания для заимствования читаются из выгрузки Конфигуратора; у исходников EDT — из экспорта 1cedtcli, который
сборка загрузила в копию ИБ (те же uuid, типы и версия Form.xml, своё преобразование .mdo не нужно).
Форма заимствуется: описание Adopted + PropertyState Form=Extended, Form.xml — корень и пустой BaseForm
(полную копию Конфигуратор молча отбрасывает), владелец — с табличными частями и <Form>;
объявления Перем уровня модуля убираются с сохранением номеров строк (переменные основного модуля видны
расширению: «уже определена»); общие реквизиты, упомянутые в модулях, заимствуются (разделители).
Ошибка «первой стадии» компиляции (тип не определён, число параметров, «уже определена») скрывает в своём
режиме неизвестные имена: модуль в этом режиме — «проверен частично» (skipped), а не «чисто».
"""
import re
import uuid
from pathlib import Path

from ...errors import InfraError
from ...sources import bsl, layout, revision
from .. import ib_tools
from ..designer_check import module_file, parse
from ..edt import list_name
from ..platform import CHECK_MODES, check_modes
from .base import result

NAME = "extcheck"
EXT = "otk_check"


class Failed(Exception):
    """Расширенная проверка не выполнилась целиком (расширение не собралось или не загрузилось, /CheckModules без
    разобранных ошибок): проверены не модули, а ничего. Решает реестр проверок: on — код 2, auto — «не проверено»."""

    def __init__(self, reason, code=None):
        super().__init__(reason)
        self.reason, self.code = reason, code


OBJECT_MODULES = ("ObjectModule", "ManagerModule", "RecordSetModule")
_POSSIBLE = re.compile(r"^" + EXT + r" (\S+) (Возможно ошибочн\w+ \w+: \"([^\"]+)\")", re.M)
_MASKING = re.compile(r"Тип не определен|фактических параметров|уже определен", re.I)
_VAR = re.compile(r"\s*(Перем|Var)\s", re.I)
# канарейка: процедура с заведомо неизвестным вызовом в конце каждого модуля; её ошибка — доказательство, что
# Конфигуратор модуль скомпилировал (форму он может молча отбросить); номера строк не сдвигает.
# В модуле формы метод без директивы компилируется только на сервере, поэтому клиентские контексты доказывает
# отдельная канарейка &НаКлиенте: каждый режим /CheckModules подтверждает только свою пару «модуль × контекст»
CANARY = "ОТККанарейкаНетТакой"
_SERVER_CANARY = f"\r\nПроцедура kc_ОТККанарейка()\r\n\t{CANARY}();\r\nКонецПроцедуры\r\n"
_CLIENT_CANARY = (f"\r\n&НаКлиенте\r\nПроцедура kc_ОТККанарейкаКлиент()\r\n\t{CANARY}();\r\nКонецПроцедуры\r\n")
# контексты, в которых компилируется клиентский метод модуля (нужна канарейка &НаКлиенте)
CLIENT_CONTEXTS = frozenset({"ThinClient", "WebClient", "OrdinaryClient"})
# метка режима в выводе /CheckModules -> контекст исполнения (ключи CHECK_MODES)
MODE_CONTEXTS = {"Тонкий клиент": "ThinClient", "Веб-клиент": "WebClient", "Сервер": "Server",
                 "Внешнее соединение": "ExternalConnection",
                 "Толстый клиент (обычное приложение)": "OrdinaryClient"}
_END = re.compile(r"\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b", re.I)
_FORM_MD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject {ns}><{tag} uuid="{uid}"><InternalInfo>'
            "<xr:PropertyState><xr:Property>Form</xr:Property><xr:State>Extended</xr:State></xr:PropertyState>"
            "</InternalInfo><Properties><ObjectBelonging>Adopted</ObjectBelonging><Name>{name}</Name><Comment/>"
            "<ExtendedConfigurationObject>{base}</ExtendedConfigurationObject><FormType>Managed</FormType>"
            "</Properties></{tag}></MetaDataObject>")


def needed_ordinary(entries):
    """Нужен ли режим обычного клиента: у проверяемых модулей есть контекст OrdinaryClient (флаг
    ClientOrdinaryApplication в конфигурации обычного приложения, модуль приложения, модуль объекта)."""
    return any("OrdinaryClient" in (e.get("contexts") or []) for e in entries)


def covered_contexts(ordinary=False):
    """Контексты, которые проверка закрывает в этом прогоне (все её режимы, кроме расширенной проверки)."""
    return frozenset(ctx for key, ctx in CHECK_MODES if (ordinary or ctx != "OrdinaryClient") and ctx)


def mode_contexts(modes):
    """Контексты исполнения по меткам режимов из вывода /CheckModules (неизвестная метка — не контекст)."""
    return frozenset(MODE_CONTEXTS[m] for m in modes or () if m in MODE_CONTEXTS)


def canary_code(entry):
    """Канарейки модуля: серверная — всегда, клиентская — когда у модуля есть клиентский контекст
    (в модуле формы метод без директивы на клиенте не компилируется, и серверная канарейка клиента не докажет)."""
    if CLIENT_CONTEXTS & set(entry.get("contexts") or ()):
        return _SERVER_CANARY + _CLIENT_CANARY
    return _SERVER_CANARY


def to_interceptors(text):
    """Все методы -> перехватчики &Вместо; возвращает (текст, номера вставленных строк в новом тексте)."""
    out, inserted = [], []
    header = bsl.header_re()
    for line in text.split("\n"):
        m = header.match(line)
        if m:
            indent, is_async, kind, name = m.group("indent", "async", "kind", "name")
            out.append(f'{indent}&Вместо("{name}")\r')
            inserted.append(len(out))
            rest = line[m.end():]
            prefix = f"{is_async} " if is_async else ""
            out.append(f"{indent}{prefix}{kind} kc_{name}({rest}")
        else:
            out.append(line)
    return "\n".join(out), inserted


def source_line(ext_line, inserted):
    return ext_line - sum(1 for i in inserted if i < ext_line)


def strip_vars(text):
    """Объявления Перем уровня модуля (и многострочные, до «;») -> пустые строки, аннотация перед ними — тоже;
    номера строк сохраняются."""
    out, inside, var = [], False, False
    header = bsl.header_re()
    for ln in text.split("\n"):
        blank = "\r" if ln.endswith("\r") else ""
        if var:                               # продолжение многострочного Перем
            out.append(blank)
            var = ";" not in ln
            continue
        if header.match(ln):
            inside = True
        elif _END.match(ln):
            inside = False
        if not inside and _VAR.match(ln):
            if out and re.match(r"\s*&", out[-1]):
                out[-1] = "\r" if out[-1].endswith("\r") else ""
            out.append(blank)
            var = ";" not in ln.split("//", 1)[0]
        else:
            out.append(ln)
    return "\n".join(out)


def _read(root, rel):
    p = Path(root) / rel
    return p.read_bytes().decode("utf-8-sig") if p.is_file() else None


def managed_form(config_root, a, lay=layout.DESIGNER):
    """Управляемая форма с описанием в исходниках формата lay (обычную во временное расширение не заимствовать)."""
    md_rel, content_rel = lay.form_files(a.collection, a.name, a.sub)
    md = _read(config_root, md_rel)
    return bool(md) and (Path(config_root) / content_rel).is_file() and not lay.ordinary_form(md, a.sub)


def _form_files(target, config_root, a, text, fmt):
    """Заимствованная форма в выгрузке временного расширения: описание, Form.xml (корень + пустой BaseForm), модуль."""
    md_rel, content_rel = layout.DESIGNER.form_files(a.collection, a.name, a.sub)
    tag = "Form" if a.sub else "CommonForm"
    base_uid = re.search(rf'<{tag} uuid="([^"]+)"', _read(config_root, md_rel) or "")
    root = re.search(r"<\?xml[^>]*>\s*<Form [^>]*>", _read(config_root, content_rel) or "")
    version = re.search(r'version="[^"]+"', root.group(0).split("<Form", 1)[1]) if root else None
    if not (base_uid and version):
        raise ValueError(f"форма {md_rel}: нет uuid описания или версии Form.xml")
    # Form.xml — в версии формата остальных файлов расширения: выгрузка ИБ новой платформой (8.5 — 2.22)
    # иначе не загружается («отличается от версии формата ранее загруженных файлов»)
    base_uid = base_uid.group(1)
    root = root.group(0).replace(version.group(0), f'version="{fmt}"')
    ib_tools.write(Path(target) / md_rel, _FORM_MD.format(ns=ib_tools._ns(fmt), tag=tag, uid=uuid.uuid4(),
                                                       name=a.sub or a.name, base=base_uid))
    ib_tools.write(Path(target) / content_rel, root + f'<BaseForm version="{fmt}"/></Form>')
    ib_tools.write(Path(target) / layout.DESIGNER.module_path(a), text)


def objects_for(config_root, units, texts, fmt=None):
    """Заимствуемые объекты: владельцы форм и модулей объектов (с ТЧ и формами), объекты по ссылкам через
    менеджеры, общие реквизиты, имена которых встречаются в модулях форм и объектов; fmt — версия формата файлов."""
    owners = {}
    for a in units:
        if a.kind != "Module" and a.collection != "CommonForms":
            forms = owners.setdefault((a.collection, a.name), [])
            if a.kind == "FormModule":
                forms.append(a.sub)
    objects = {}
    for (coll, name), forms in sorted(owners.items()):
        o = ib_tools.adopted_object(config_root, coll, name, tabular=True, forms=forms, fmt=fmt)
        if o:
            objects[(coll, name.lower())] = o
    for coll, name in sorted(ib_tools.referenced_objects(texts)):
        if (coll, name.lower()) not in objects:
            o = ib_tools.adopted_object(config_root, coll, name, fmt=fmt)
            if o:
                objects[(coll, name.lower())] = o
    if owners or any(a.collection == "CommonForms" for a in units):
        words = set(re.findall(r"[\wА-Яа-яЁё]+", "\n".join(texts)))
        for name in layout.DESIGNER.object_names(config_root, "CommonAttributes"):
            if name in words:
                o = ib_tools.adopted_object(config_root, "CommonAttributes", name, fmt=fmt)
                if o:
                    objects[("CommonAttributes", name.lower())] = o
    return list(objects.values())


def ib_names(source, lay, units, texts):
    """Строки -listFile частичной выгрузки ИБ вместо экспорта EDT: то, что objects_for и _check прочтут из
    выгрузки для модулей EDT units, — корень и язык, общие модули, общие формы, владельцы модулей форм и объектов
    с формами, объекты по ссылкам через менеджеры (есть в исходниках), общие реквизиты, упомянутые в текстах."""
    names = {"Configuration"}
    lang = re.search(r"<defaultLanguage>Language\.([^<]+)</defaultLanguage>",
                     layout.read_text(Path(source) / lay.config_file) or "")
    if lang:
        names.add(list_name("Languages", lang.group(1)))
    scan = False
    for m in units:
        a = lay.parse(m["path"])
        if a is None:
            continue
        names.add(list_name(a.collection, a.name))
        if a.kind == "FormModule" and a.sub:
            names.add(list_name(a.collection, a.name, a.sub))
        scan = scan or a.kind != "Module" or a.collection == "CommonForms"
    known = {}
    for coll, name in ib_tools.referenced_objects(texts):
        if coll not in known:
            known[coll] = {n.lower(): n for n in lay.object_names(source, coll)}
        real = known[coll].get(name.lower())
        if real:
            names.add(list_name(coll, real))
    if scan:
        words = set(re.findall(r"[\wА-Яа-яЁё]+", "\n".join(texts)))
        names |= {list_name("CommonAttributes", n) for n in lay.object_names(source, "CommonAttributes") if n in words}
    return sorted(n for n in names if n)


def _check(platform, run_dir, props, config_root, units, side, objects=(), fmt=None, ordinary=False):
    """units — {путь в выгрузке: (Addr, описание общего модуля или None, текст, модуль плана)}. Возвращает словарь:
    findings [(путь или None, строка, сообщение, режим или None)] — по записи на режим; masked {путь: режимы с
    ошибкой первой стадии}; compiled {путь: контексты, в которых канарейка доказала компиляцию}. Не загрузилось или
    не проверилось — {"failed": причина}."""
    target = Path(run_dir) / f"extcheck-{side}"
    gen, maps, files, objects = {}, {}, [], list(objects)
    for rel, (a, xml, text, entry) in units.items():
        canary = canary_code(entry)
        if a.kind == "Module":
            flags = {f: (re.search(rf"<{f}>(\w+)</{f}>", xml) or [None, "false"])[1]
                     for f in ("Global", "ClientManagedApplication", "Server", "ExternalConnection",
                               "ClientOrdinaryApplication", "ServerCall", "Privileged")}
            uid = re.search(r'<CommonModule uuid="([^"]+)"', xml).group(1)
            flags["__head__"] = (f"<ObjectBelonging>Adopted</ObjectBelonging><Name>{a.name}</Name><Comment/>"
                                 f"<ExtendedConfigurationObject>{uid}</ExtendedConfigurationObject>")
            new_text, maps[rel] = to_interceptors(text)
            gen[a.name] = (flags, new_text + canary)
            continue
        new_text, maps[rel] = to_interceptors(strip_vars(text))
        files.append((a, new_text + canary))
        if a.collection == "CommonForms":   # общая форма — объект верхнего уровня (описание пишет _form_files)
            objects.append({"collection": "CommonForms", "name": a.name, "tag": "CommonForm", "xml": ""})
    ib_tools.generate_test_extension(target, props, name=EXT, prefix="kc_", modules=gen, objects=objects)
    try:
        for a, new_text in files:
            if a.kind == "FormModule":
                _form_files(target, config_root, a, new_text, fmt or props["format"])
            else:
                ib_tools.write(target / layout.DESIGNER.module_path(a), new_text)
    except ValueError as e:
        return {"failed": str(e)}
    rc, _, out = platform.load_dir(target, extension=EXT, label=f"extcheck-load-{side}")
    if rc:
        return {"failed": f"загрузка {EXT}: {out[-300:]}"}
    rc, _, out = platform.designer("/CheckModules", *(check_modes(ordinary)), "-Extension", EXT,
                                   label=f"extcheck-{side}")
    findings, masked, compiled = [], {}, {}
    for f in parse(out):
        rel = f["file"]
        msg = f["message"].split(" (Проверка:")[0].strip()
        if CANARY in msg:
            if rel:                    # канарейка доказывает компиляцию только в режимах из её сообщения
                compiled[rel] = compiled.get(rel, frozenset()) | mode_contexts(f["modes"])
            continue
        line = source_line(f["line"], maps.get(rel, [])) if rel else f["line"]
        findings += [(rel, line, msg if rel else f"{f['object']}: {msg}", mode) for mode in f["modes"] or [None]]
        if rel and _MASKING.search(msg):
            masked.setdefault(rel, set()).update(f["modes"] or ["?"])
    for m in _POSSIBLE.finditer(out):
        findings.append((module_file(m.group(1)), None, m.group(2), None))
    if rc and not findings and not compiled:      # Конфигуратор не проверил — не «чисто»
        return {"failed": f"/CheckModules: rc={rc} без разобранных ошибок: {out[-300:]}"}
    return {"findings": findings, "masked": masked, "compiled": compiled}


_POSSIBLE_ANY = re.compile(r"^(\S+) (\S+) (Возможно ошибочн\w+ \w+: \"([^\"]+)\")", re.M)


def run_extension(platform, extension, entries, read_text, lay=layout.DESIGNER, ordinary=False):
    """Расширенная проверка проекта-расширения целиком. entries — модули плана (для привязки находок),
    read_text(путь в исходниках) -> текст модуля или None; пути — в формате исходников lay. ordinary — режим
    обычного клиента (см. needed_ordinary). Возвращает (результаты, примечание)."""
    rc, _, out = platform.designer("/CheckModules", *(check_modes(ordinary)), "-Extension", extension,
                                   label="extcheck-extension")
    by_path = {e["path"]: e for e in entries}
    results = []
    for f in parse(out, lay=lay):
        entry = by_path.get(f["file"]) or {"path": f["file"], "owner": f["object"]}
        modes = f" ({', '.join(f['modes'])})" if f["modes"] else ""
        results.append(result(NAME, "extcheck", entry, "Designer", "compile-error", f["message"] + modes, f["line"]))
    for m in _POSSIBLE_ANY.finditer(out):
        if m.group(1) != extension:
            continue
        rel = module_file(m.group(2), lay=lay)
        line = None
        text = read_text(rel) if rel else None
        for i, ln in enumerate((text or "").split("\n"), 1):
            if m.group(4) in ln:
                line = i
                break
        entry = by_path.get(rel) or {"path": rel, "owner": m.group(2)}
        results.append(result(NAME, "extcheck", entry, "Designer", "compile-error", m.group(3), line))
    if rc and not results:
        raise Failed(f"проверка расширения {extension}: rc={rc} без разобранных ошибок: {out[-300:]}")
    return results, ""


def run(platform, run_dir, config_root, entries, read_base, read_head, read_xml, lay=layout.DESIGNER,
        ordinary=False, covered=None):
    """entries — изменённые модули плана: общие, а в выгрузке Конфигуратора и модули управляемых форм, объектов,
    менеджеров, наборов записей. ordinary — режим обычного клиента (см. needed_ordinary). covered — необязательный
    словарь: заполняется парами «путь модуля -> контексты, в которых канарейка доказала компиляцию». Возвращает
    (результаты, примечание); не выполнилось целиком — Failed."""
    if not entries:
        return [], ""
    props = ib_tools.base_properties(config_root, getattr(platform, "bin", None))
    fmt = props["format"]
    by_rel = {}
    for e in entries:
        rel = lay.to_designer(e["path"])
        a = layout.DESIGNER.parse(rel) if rel else None
        if a is not None:
            by_rel[rel] = (a, e)

    def unit(rel, a, e, text):
        return a, (read_xml(e) if a.kind == "Module" else None), text, e

    head = {rel: unit(rel, a, e, read_head(e)) for rel, (a, e) in by_rel.items()}
    base = {}
    for rel, (a, e) in by_rel.items():
        text = read_base(e) if e["status"] != "A" else None
        if text:
            base[rel] = unit(rel, a, e, text)
    # объекты основной конфигурации, на которые ссылаются модули, заимствуются: иначе расширенная проверка в
    # расширении считает их «возможно ошибочными свойствами»; один набор для обеих редакций
    texts = [t for _, _, t, _ in head.values()] + [t for _, _, t, _ in base.values()]
    objects = objects_for(config_root, [a for a, _ in by_rel.values()], texts, fmt)
    try:
        after = _check(platform, run_dir, props, config_root, head, "after", objects, fmt, ordinary)
        if "failed" in after:             # временное расширение не загрузилось — модули не проверены, а не «чисто»
            raise Failed(after["failed"])
        before = _check(platform, run_dir, props, config_root, base, "before", objects, fmt, ordinary) if base else \
            {"findings": [], "masked": {}, "compiled": {}}
    finally:
        platform.delete_extension(EXT, label="extcheck-delete")
    return compare(by_rel, head, base, after, before, covered)


def compared_contexts(by_rel, base, after, before):
    """Контексты, в которых диагностику модуля можно сравнить с «было»: канарейка подтвердила компиляцию обеих
    редакций (сравнение находок идёт с before.findings, поэтому подтверждённый только в «после» режим новой
    находки не доказывает). Новый модуль сравнивать не с чем — его режимы подтверждает одна редакция «после»."""
    out = {}
    for rel in by_rel:
        found = after["compiled"].get(rel) or frozenset()
        out[rel] = found if rel not in base else found & (before["compiled"].get(rel) or frozenset())
    return out


def _fill_covered(by_rel, compared, skipped, covered):
    """Пары «путь модуля в плане -> контексты» по сравнимым режимам: контекст, подтверждённый только в одной
    редакции, пару не закрывает (сравнить его диагностику не с чем). Модуль без сравнения с «было» — не покрыт."""
    for rel, (_, entry) in by_rel.items():
        found = compared.get(rel) if rel not in skipped else None
        if found:
            covered[entry["path"]] = found


def _uncompared_notes(by_rel, compared, after, skipped, uncompared):
    """Записи «нет сравнения с «было»»: режим подтверждён канарейкой только в редакции «после» — его диагностику
    не с чем сравнить, и находкой она не выдаётся (uncompared — число скрытых находок по режимам)."""
    out = []
    for rel in sorted(compared):
        lost = (after["compiled"].get(rel) or frozenset()) - compared[rel]
        if not lost or rel in skipped:
            continue
        hidden = sum(uncompared.get(rel, {}).values())
        out.append(result(NAME, "extcheck", by_rel[rel][1], "Designer", "skipped",
                          f"нет сравнения с «было» ({', '.join(sorted(lost))}): режим подтверждён только в "
                          "редакции «после» — его диагностика не выдаётся за новую"
                          + (f" (скрыто: {hidden})" if hidden else "")))
    return out


def _new_findings(by_rel, base, after, before, compared, skipped):
    """Находки «стало», которые доказуемо новые: их не было в «было» (ключ — модуль, сообщение, режим) и режим
    сравним с «было». Возвращает ({ключ: режимы}, число отброшенных «Возможно…», {модуль: {режим: число}} —
    находки режимов, подтверждённых только в «после»)."""
    old_keys = {}
    for rel, _, msg, mode in before["findings"]:
        old_keys[(rel, msg, mode)] = old_keys.get((rel, msg, mode), 0) + 1
    new, possible_dropped, uncompared = {}, 0, {}
    for rel, line, msg, mode in after["findings"]:
        if old_keys.get((rel, msg, mode), 0) > 0:
            old_keys[(rel, msg, mode)] -= 1
            continue
        if rel in skipped:
            continue
        if msg.startswith("Возможно") and rel in by_rel and rel not in base:
            possible_dropped += 1         # без «было» не отличить от имён метаданных строкой, стилей и т. п.
            continue
        ctx = MODE_CONTEXTS.get(mode)
        if ctx is not None and rel in compared and ctx in after["compiled"].get(rel, ()) \
                and ctx not in compared[rel]:
            # режим подтверждён только в «после»: сравнить его диагностику с «было» нечем — не новая находка
            hidden = uncompared.setdefault(rel, {})
            hidden[mode] = hidden.get(mode, 0) + 1
            continue
        new.setdefault((rel, line, msg), []).append(mode)
    return new, possible_dropped, uncompared


def compare(by_rel, head, base, after, before, covered=None):
    """Новые замечания «стало» относительно «было» (ключ — модуль, сообщение, режим) и примечания по модулям:
    «проверен частично» (маскировка), «не скомпилирован» (нет канарейки), нет сравнения с «было». covered —
    необязательный словарь пар «путь модуля -> контексты с доказанной компиляцией» для отчёта о покрытии."""
    results, notes, skipped = [], [], {}
    if "failed" in before:                # «было» не проверилось: замечания модулей с базой не отличить от старых
        for rel in base:
            skipped[rel] = f"не сравнить с «было» ({before['failed'][:200]})"
        before = {"findings": [], "masked": {}, "compiled": {}}
    compared = compared_contexts(by_rel, base, after, before)
    # без канарейки и без новых ошибок — модуль отброшен; ошибка разбора останавливает компиляцию до канарейки,
    # поэтому модуль с новыми ошибками — скомпилирован
    dropped = {rel for rel in by_rel if rel not in after["compiled"] and rel not in after["masked"]}
    new, possible_dropped, uncompared = _new_findings(by_rel, base, after, before, compared, skipped)
    for rel in dropped - {r for r, _, msg in new if not msg.startswith("Возможно")}:
        skipped[rel] = "модуль не скомпилирован во временном расширении (Конфигуратор его отбросил)"
    new = {key: modes for key, modes in new.items() if key[0] not in skipped}
    for (rel, line, msg), modes in new.items():
        entry = by_rel.get(rel, (None, None))[1]
        if rel is None:                   # ошибка без модуля (вид объекта не из словаря) — не «чисто»
            results.append(result(NAME, "extcheck", None, "Designer", "skipped", f"ошибка без модуля: {msg}"))
            continue
        if line is None:
            ident = re.search(r'"([^"]+)"', msg)
            text = head[rel][2] if rel in head else ""
            if ident:
                for i, ln in enumerate(text.split("\n"), 1):
                    if ident.group(1) in ln:
                        line = i
                        break
        shown = sorted(m for m in modes if m)
        results.append(result(NAME, "extcheck", entry, "Designer", "compile-error",
                              msg + (f" ({', '.join(shown)})" if shown else ""), line))
    for rel in sorted(set(after["masked"]) | set(before["masked"])):
        if rel in by_rel and rel not in skipped:
            modes = sorted(after["masked"].get(rel, set()) | before["masked"].get(rel, set()))
            results.append(result(NAME, "extcheck", by_rel[rel][1], "Designer", "skipped",
                                  f"проверен частично ({', '.join(modes)}): ошибка первой стадии компиляции скрывает "
                                  "в этом режиме неизвестные имена"))
    results += _uncompared_notes(by_rel, compared, after, skipped, uncompared)
    for rel, why in sorted(skipped.items()):
        results.append(result(NAME, "extcheck", by_rel[rel][1], "Designer", "skipped", why))
    if covered is not None:               # покрытие — по парам: режим без канарейки в обеих редакциях не подтверждён
        _fill_covered(by_rel, compared, skipped, covered)
    if possible_dropped:
        notes.append(f"extcheck: новые модули без «было» — «Возможно ошибочн…» не показаны ({possible_dropped})")
    return results, "; ".join(notes)


class ExtCheck:
    """Расширенная проверка: проект-расширение — целиком (run_extension), конфигурация — изменённые общие
    модули (в выгрузке Конфигуратора и модули форм и объектов) во временном расширении, новые замечания
    относительно «было» (run). Покрытие — парами «модуль × контекст» в ctx.covered: канарейка подтверждает
    компиляцию только в тех режимах, где её ошибка есть в выводе (снимают «не покрыто этим прогоном»)."""
    name = NAME
    checks = ("extcheck",)

    def select(self, ctx):
        return ctx.modules if ctx.modules and ctx.gate.ready("extcheck", ctx.have) else None

    def run(self, ctx, items):
        """Проверка выполнилась не целиком — решение реестра проверок, а не «модули чисты»: в on — код 2, в auto —
        «не проверено» с check extcheck; per-module skipped при полном сбое не выдаются."""
        try:
            return self._run(ctx, items)
        except Failed as e:
            ctx.gate.failed(NAME, e.reason, e.code)
            return [], ""

    def _run(self, ctx, items):
        plan = ctx.plan
        source = Path(ctx.tree_root) / plan["source"]
        extension = ctx.cfg["project"].get("extension")
        lay = layout.get(plan.get("sourceFormat"))
        if extension:
            def read_ext(rel):
                p = source / rel
                return layout.read_text(p)

            ordinary = needed_ordinary(items)
            res, note = run_extension(ctx.platform, extension, items, read_ext, lay, ordinary)
            ctx.ext_contexts = covered_contexts(ordinary)
            # расширение загружено в ИБ целиком и проверяется как есть (без перехватчиков и канарейки): модуль,
            # который не скомпилировался, даёт ошибку, «частично проверенных» здесь нет — режимы проверены все
            ctx.covered.update({m["path"]: ctx.ext_contexts for m in items})
            return res, note
        changed = [m for m in items if m.get("reason") == "changed"
                   and m["kind"] in ("CommonModule", "FormModule") + OBJECT_MODULES]
        read_base = revision.read_base(ctx.repo, plan)

        def base_text(e):
            return read_base(e.get("renamedFrom") or e["path"]) if e["status"] != "A" else None

        def head_text(e):
            return (source / e["path"]).read_bytes().decode("utf-8-sig")

        # исходники EDT: описания для заимствования — из экспорта сборки, а без него — частичной выгрузкой
        # ИБ (метаданные в ИБ те же: без экспорта сборка меняет только модули)
        ib_dump = changed and lay is layout.EDT and not ctx.designer_dump
        try:
            if ib_dump:
                units = [m for m in changed if self._supported(source, lay, m)]
                texts = [t for m in units for t in (head_text(m), base_text(m)) if t]
                root = ctx.designer_root(ib_names(source, lay, units, texts)) if units else ctx.config_root
            else:
                root = ctx.designer_root() if changed else ctx.config_root
        except InfraError as e:
            raise Failed(f"{'выгрузка ИБ' if ib_dump else 'экспорт EDT'}: {e}", e.code) from e
        if not ib_dump:
            units = [m for m in changed if self._supported(root, lay, m)]
        ordinary = needed_ordinary(units)
        res, note = run(
            ctx.platform, ctx.run_dir, root, units,
            read_base=base_text, read_head=head_text,
            read_xml=lambda e: (root / layout.DESIGNER.object_file("CommonModules", e["owner"].split(".", 1)[1]))
            .read_bytes().decode("utf-8-sig"), lay=lay, ordinary=ordinary, covered=ctx.covered)
        ctx.ext_contexts = covered_contexts(ordinary)
        return res, note

    @staticmethod
    def _supported(root, lay, m):
        """Общий модуль, модуль управляемой формы, модуль объекта, менеджера, набора записей. root — выгрузка
        Конфигуратора (у исходников EDT — экспорт, см. ExtCheck.run) или сами исходники lay; путь модуля — в формате
        lay."""
        if m["kind"] == "CommonModule":
            return True
        if m["kind"] not in ("FormModule",) + OBJECT_MODULES:
            return False
        if layout.detect(root) is lay:
            a, root_lay = lay.parse(m["path"]), lay
        else:
            rel = lay.to_designer(m["path"])
            a, root_lay = (layout.DESIGNER.parse(rel) if rel else None), layout.DESIGNER
        return a is not None and (a.kind != "FormModule" or managed_form(root, a, root_lay))
