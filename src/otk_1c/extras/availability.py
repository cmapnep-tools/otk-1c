"""Таблица фактической доступности методов и свойств глобального контекста и типов (`Новый Т`) по контекстам
компиляции (6.13).

Справка синтакс-помощника расходится с компилятором (ЗначениеВДанныеФормы она не указывает во внешнем соединении,
модальные Вопрос и Предупреждение — в веб-клиенте, НачатьУдалениеФайлов — на сервере), поэтому источник истины —
Конфигуратор: каждое имя — в своём общем модуле временного расширения (ошибки числа параметров глушат
«не определена» во всём модуле, а сами означают, что имя найдено), /CheckModules по режимам; «не определена»
в модуле имени — недоступно в этом контексте; у типа — «Тип не определен» (`Новый Т` без параметров: число
параметров конструктора компилятор не проверяет). Список имён — из справки той же платформы.
Режим -WebClient доступность типов не проверяет (пропускает и Запрос, и ТаблицаЗначений — проверено на 8.3.27), поэтому
веб-клиент типов — замер в настоящем веб-клиенте (`availability/webclient/<версия>.json`):
доступен, если его не отверг тонкий клиент и `Новый(Имя)` в веб-клиенте не дал «Тип не
определен». Справка ошибается в обе стороны (ПараметрВыбора в веб-клиенте есть, хотя она его не указывает). Нет замера
той же линейки или тип роняет веб-клиент (Рамка) — по справке: тонкий клиент и строка доступности «веб-клиент»
(без строки — по тонкому клиенту).

`otk availability build` — таблица для платформы проверки в кэш пользователя (`contexts.user_tables_dir`);
расширение otk_avail создаётся в копии ИБ проекта и удаляется. В поставке — таблицы 8.3.27 и 8.5.1
(`--out src/otk_1c/availability`).
"""
import json
import re
from pathlib import Path

from ..probes import ib_tools, syntax
from ..probes.platform import check_mode, platform_version
from ..sources import contexts

MODES = tuple((check_mode(ctx), ctx) for ctx in ("Server", "ThinClient", "WebClient", "ExternalConnection"))
EXT = "otk_avail"
WEB_DIR = Path(__file__).parent.parent / "availability" / "webclient"


def table_version(version):
    """Версия таблицы — три компонента (8.3.27): по строке версии платформы; None — строки версии нет."""
    found = re.search(r"\d+\.\d+\.\d+", version or "")
    return found.group(0) if found else None


def webclient_types(version):
    """Замер веб-клиента ближайшей версии той же линейки: (версия замера, {тип: defined | undefined | crash})
    или None."""
    files = {f.stem: f for f in WEB_DIR.glob("*.json")}
    best = contexts.nearest_table(version, files)
    if not best or contexts.version_key(best)[:2] != contexts.version_key(version)[:2]:
        return None
    return best, json.loads(files[best].read_text(encoding="utf-8"))["types"]


def apply_webclient(data, measured):
    """Веб-клиент типов таблицы data по замеру measured (webclient_types): тонкий клиент и не «Тип не определен»;
    тип, которого нет в замере или который роняет клиент, — как построено (тонкий клиент ∩ справка)."""
    if not measured:
        return data
    version, results = measured
    for name, ctx in data["types"].items():
        state = results.get(name)
        if state in ("defined", "undefined"):
            web = state == "defined" and "ThinClient" in ctx
            data["types"][name] = [c for c in data["contexts"] if c in ctx and c != "WebClient" or
                                   c == "WebClient" and web]
    data["typesWebClient"] = f"замер веб-клиента {version}"
    return data


def build(platform, config_root, out_dir, log=print):
    """Строит таблицу для платформы platform (Platform с копией ИБ), язык расширения — из выгрузки config_root.
    Возвращает путь записанного файла."""

    version = table_version(platform_version(platform.bin))
    syntax.add_platform(platform.bin)
    # таблица — ровно своей платформы: справку другой версии не подставлять
    ix = syntax.open_exact(syntax.index.version_of_dir(str(platform.bin)) or version, str(platform.bin))
    props = ib_tools.base_properties(config_root)
    items = {}
    for r in ix.db.execute("SELECT name, name_en, kind FROM entries WHERE owner = ? AND kind IN ('method','property')",
                           (syntax.index.GLOBAL,)).fetchall():
        if re.fullmatch(r"[\wА-Яа-яЁё]+", r["name"] or ""):
            items.setdefault(r["name"], (r["kind"], r["name_en"]))
    types, help_web = {}, {}
    for r in ix.db.execute("SELECT name, name_en FROM entries WHERE kind = 'ctor'").fetchall():
        if re.fullmatch(r"[\wА-Яа-яЁё]+", r["name"] or ""):
            types.setdefault(r["name"], r["name_en"])
    for r in ix.db.execute("SELECT name, availability FROM entries WHERE kind IN ('type', 'ctor') "
                           "AND availability <> ''").fetchall():
        if r["name"] in types and r["name"] not in help_web:
            help_web[r["name"]] = "веб-клиент" in r["availability"].lower()
    flags = {"Server": "true", "ClientManagedApplication": "true", "ExternalConnection": "true"}
    modules, by_module = {}, {}
    for n, (name, (kind, _)) in enumerate(sorted(items.items())):
        mod = f"ka_m{n}"
        by_module[mod] = name
        body = f"\tХ = {name};" if kind == "property" else f"\tХ = {name}();"
        modules[mod] = (dict(flags), f"Процедура П() Экспорт\n{body}\nКонецПроцедуры\n")
    for n, name in enumerate(sorted(types)):
        mod = f"ka_t{n}"
        by_module[mod] = ("type", name)
        modules[mod] = (dict(flags), f"Процедура П() Экспорт\n\tХ = Новый {name};\nКонецПроцедуры\n")
    target = Path(platform.run_dir) / "avail-ext"
    ib_tools.generate_test_extension(target, props, name=EXT, prefix="ka_", modules=modules)
    log(f"справка {ix.version}: имён {len(items)}, типов {len(types)}; загрузка временного расширения {EXT}")
    rc, _, out = platform.load_dir(target, extension=EXT, label="avail-load")
    if rc:
        raise RuntimeError(f"загрузка расширения {EXT}: {out[-500:]}")
    missing = {}
    try:
        for mode, ctx in MODES:
            rc, _, out = platform.designer("/CheckModules", mode, "-Extension", EXT, label="avail-check", timeout=3600)
            miss = set()
            for m in re.finditer(rf"^\{{{EXT} [^.]+\.(ka_[mt]\d+)\.[^(]+\(\d+,\d+\)\}}: (.+)$", out, re.M):
                key = by_module.get(m.group(1))
                name = key[1] if isinstance(key, tuple) else key
                if name and "не определен" in m.group(2) and f"({name})" in m.group(2):
                    miss.add(key)
            missing[ctx] = miss
            log(f"{mode}: недоступно {len(miss)}")
        for name in types:            # веб-клиент типов: тонкий клиент Конфигуратора и справка
            if ("type", name) in missing["ThinClient"] or not help_web.get(name, True):
                missing["WebClient"].add(("type", name))
    finally:
        platform.delete_extension(EXT, label="avail-delete")
    contexts = [c for _, c in MODES]
    data = {"platform": ix.version, "contexts": contexts,
            "methods": {n: [c for c in contexts if n not in missing[c]] for n, (k, _) in sorted(items.items())
                        if k == "method"},
            "properties": {n: [c for c in contexts if n not in missing[c]] for n, (k, _) in sorted(items.items())
                           if k == "property"},
            "english": {n: en for n, (_, en) in sorted(items.items()) if en},
            "types": {n: [c for c in contexts if ("type", n) not in missing[c]] for n in sorted(types)},
            "typesEnglish": {n: en for n, en in sorted(types.items()) if en}}
    apply_webclient(data, webclient_types(version))
    out_file = Path(out_dir) / f"{version}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return out_file
