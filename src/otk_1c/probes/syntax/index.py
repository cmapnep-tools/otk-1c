"""Индекс справки платформы в SQLite (FTS5): сборка и запросы.

Один файл `<кэш>/syntax/<версия платформы>.db` на каждую установленную платформу; кэш — `OTK_CACHE`, иначе
`%LOCALAPPDATA%/otk` (Windows) или `~/.cache/otk`. Индекс собирается из справки
установленной у пользователя платформы при первом обращении к версии (несколько секунд) и пересобирается при
смене схемы или исходных `.hbk` (размер, время изменения — в meta); данные справки в поставку ОТК не входят.

Справка «недоступна» только в крайнем случае: своя версия -> индекс в кэше (платформа удалена) -> ближайшая
версия той же линейки (установленная, из `environment.helpDir` или в кэше) с примечанием. `helpAuto = false` —
индекс сам не строится и не обновляется: предупреждение с командой `otk platform-help build`.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import time

from ...paths import cache_root
from ..platform import install_roots
from . import hbk

SCHEMA = "4"
EXTRA_BINS: list[str] = []      # каталоги bin платформ вне стандартных мест (environment.platform) — add_platform
HELP_DIRS: list[str] = []       # копии .hbk (environment.helpDir): каталог <версия> или каталог подкаталогов <версия>
AUTO = True                     # environment.helpAuto: строить и обновлять индекс самому
SOURCES = {"cntx": "shcntx_ru.hbk", "lang": "shlang_ru.hbk", "query": "shquery_ru.hbk"}
GLOBAL = "Глобальный контекст"


def version_key(v: str) -> int:
    """'8.3.25' / '8.3.27.2342' / 'Version8_3_21' -> сравнимое число по первым трём частям."""
    nums = [int(x) for x in re.findall(r"\d+", v or "")][:3]
    if not nums:
        return 0
    nums += [0] * (3 - len(nums))
    return nums[0] * 1_000_000 + nums[1] * 10_000 + nums[2]


def version_str(v: str) -> str:
    return ".".join(re.findall(r"\d+", v or "")[:3])


def cache_dir() -> str:
    return os.path.join(cache_root(), "syntax")


def _roots() -> list[str]:
    """Каталоги установки платформы: оба режима установки Windows; Linux — /opt/1cv8/<arch>."""
    return [str(r) for r in install_roots()]


def add_platform(bin_dir) -> None:
    """Платформа вне стандартных мест (настройка environment.platform)."""
    if bin_dir and str(bin_dir) not in EXTRA_BINS:
        EXTRA_BINS.append(str(bin_dir))


def configure(env: dict) -> None:
    """Настройки [environment]: platform, helpDir (строка или список), helpAuto (по умолчанию true)."""
    global AUTO
    add_platform(env.get("platform"))
    dirs = env.get("helpDir") or []
    for d in [dirs] if isinstance(dirs, str) else dirs:
        if d and str(d) not in HELP_DIRS:
            HELP_DIRS.append(str(d))
    AUTO = bool(env.get("helpAuto", True))


def _vsort(v: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", v)]


def version_of_dir(path: str) -> str:
    """Версия платформы по каталогу справки: …/8.3.27.2342/bin или …/8.3.27.2342 -> '8.3.27.2342' ('' — нет)."""
    d = os.path.normpath(path)
    if os.path.basename(d).lower() == "bin":
        d = os.path.dirname(d)
    name = os.path.basename(d)
    return name if len(_vsort(name)) >= 3 else ""


def installed_platforms() -> dict[str, str]:
    """Версия -> каталог со справкой: bin установленной платформы или копия .hbk из environment.helpDir."""
    out = {}
    found = [os.path.dirname(d) for root in _roots()
             for pattern in (("*", "bin", SOURCES["cntx"]), ("*", SOURCES["cntx"]))
             for d in glob.glob(os.path.join(root, *pattern))]
    found += [os.path.dirname(d) for h in HELP_DIRS
              for pattern in ((SOURCES["cntx"],), ("*", SOURCES["cntx"]), ("*", "bin", SOURCES["cntx"]))
              for d in glob.glob(os.path.join(glob.escape(h), *pattern))]
    found += [b for b in EXTRA_BINS if os.path.exists(os.path.join(b, SOURCES["cntx"]))]
    for bin_dir in found:
        v = version_of_dir(bin_dir)
        if v:
            out[v] = bin_dir
    return dict(sorted(out.items(), key=lambda kv: _vsort(kv[0])))


def _read_meta(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {}
    c = sqlite3.connect(db_path)
    try:
        return dict(c.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return {}
    finally:
        c.close()  # иначе Windows не даст заменить файл при пересборке


def sources_signature(bin_dir: str) -> str:
    """Размер и время изменения исходных .hbk: переустановка той же версии (патч) — индекс перестраивается."""
    out = {}
    for name in SOURCES.values():
        p = os.path.join(bin_dir, name)
        if os.path.exists(p):
            st = os.stat(p)
            out[name] = [st.st_size, int(st.st_mtime)]
    return json.dumps(out, sort_keys=True)


def cached_indexes() -> dict[str, dict]:
    """Версия -> meta индексов в кэше с текущей схемой (в том числе от удалённых платформ)."""
    out = {}
    for p in glob.glob(os.path.join(glob.escape(cache_dir()), "*.db")):
        meta = _read_meta(p)
        if meta.get("schema") == SCHEMA:
            out[os.path.basename(p)[:-3]] = meta
    return dict(sorted(out.items(), key=lambda kv: _vsort(kv[0])))


def _split_member(title: str) -> tuple[str, str, str, str]:
    ru, en = hbk.split_title(title)
    owner, _, name = ru.rpartition(".")
    owner_en, _, name_en = en.rpartition(".")
    return owner, name, owner_en, name_en


def _words(name: str) -> str:
    """«ВызватьПаузу CallSleep» -> «Вызвать Паузу Call Sleep»: слова имени для полнотекстового поиска."""
    return re.sub(r"(?<=[а-яёa-z0-9])(?=[А-ЯЁA-Z])", " ", name)


def build(version: str, bin_dir: str, db_path: str) -> int:
    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE entries(id INTEGER PRIMARY KEY, kind TEXT, owner TEXT, owner_en TEXT, name TEXT, name_en TEXT,
            name_lc TEXT, name_en_lc TEXT, variant TEXT, title TEXT, syntax TEXT, params TEXT, returns TEXT,
            description TEXT, availability TEXT, note TEXT, since TEXT, since_key INTEGER, versions TEXT,
            body TEXT, path TEXT);
        CREATE INDEX entries_name ON entries(name_lc);
        CREATE INDEX entries_name_en ON entries(name_en_lc);
        CREATE VIRTUAL TABLE entries_fts USING fts5(title, words, description, tokenize='unicode61');
    """)
    n = 0
    for source, file_name in SOURCES.items():
        path = os.path.join(bin_dir, file_name)
        if not os.path.exists(path):
            continue
        z = hbk.open_storage(path)
        for name in z.namelist():
            if name.endswith((".st", "__categories__")) or (source == "cntx" and not name.endswith(".html")):
                continue
            kind = hbk.kind_of(name, source)
            if not kind:
                continue
            page = z.read(name).decode("utf-8-sig", "replace")
            e = hbk.parse_page(page) if source == "cntx" else hbk.parse_prose(page)
            if not e:
                continue
            variant = ""
            if kind == "type":
                owner, owner_en = "", ""
                nm, nm_en = hbk.split_title(e["title"])
            elif kind == "ctor":
                nm, nm_en = hbk.split_title(e["owner_title"])
                owner, owner_en = "", ""
                variant = e["title"][len(nm) + 1:] if e["title"].startswith(nm + ".") else e["title"]
            elif kind in ("method", "property", "event"):
                owner, nm, owner_en, nm_en = _split_member(e["title"])
            else:
                owner, owner_en, nm, nm_en = "", "", e["title"], ""
            # lower() в Python: в SQLite он не понижает кириллицу
            cur = db.execute(
                "INSERT INTO entries(kind,owner,owner_en,name,name_en,name_lc,name_en_lc,variant,title,syntax,params,"
                "returns,description,availability,note,since,since_key,versions,body,path) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, owner, owner_en, nm, nm_en, nm.lower(), nm_en.lower(), variant, e["title"], e["syntax"],
                 e["params"], e["returns"], e["description"], e["availability"], e["note"], e["since"],
                 version_key(e["since"]) if e["since"] else None, e["versions"],
                 e["body"] if source != "cntx" else "", f"{file_name}/{name}"))
            db.execute("INSERT INTO entries_fts(rowid,title,words,description) VALUES(?,?,?,?)",
                       (cur.lastrowid, e["title"], _words(nm + " " + nm_en), e["description"] or e["body"][:4000]))
            n += 1
    # у части конструкторов строки версии нет — берём версию типа
    db.execute("""UPDATE entries SET since = t.since, since_key = t.since_key
                  FROM (SELECT name, since, since_key FROM entries WHERE kind = 'type' AND since <> '') AS t
                  WHERE entries.kind = 'ctor' AND entries.since = '' AND entries.name = t.name""")
    db.executemany("INSERT INTO meta VALUES(?,?)", [
        ("schema", SCHEMA), ("platform", version), ("bin", bin_dir), ("entries", str(n)),
        ("sources", sources_signature(bin_dir)),
        ("built", time.strftime("%Y-%m-%d %H:%M:%S"))])
    db.commit()
    db.close()
    os.replace(tmp, db_path)
    return n


class Index:
    def __init__(self, version: str, db_path: str):
        self.version = version
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # lower() в SQLite не понижает кириллицу
        self.db.create_function("pylower", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)

    def lookup(self, name: str, kind: str | None = None, owner: str | None = None) -> list[sqlite3.Row]:
        """Точное совпадение имени (рус. или англ., без учёта регистра)."""
        q = "SELECT * FROM entries WHERE (name_lc = ? OR name_en_lc = ?)"
        args: list = [name.strip().lower()] * 2
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        if owner:
            q += " AND (pylower(owner) = ? OR pylower(owner_en) = ? OR owner LIKE ?)"
            o = owner.strip()
            args += [o.lower(), o.lower(), o]
        return self.db.execute(q + " ORDER BY kind, owner LIMIT 200", args).fetchall()

    def search(self, query: str, kind: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        rows = self.lookup(query, kind)[:limit]
        words = re.findall(r"\w+", query, re.U)
        # грубое усечение окончаний: «разделить строку» находит «Разделяет строку»
        terms = [f'"{w[:max(4, len(w) - 3)] if len(w) >= 5 and not re.search(r"[A-Za-z]", w) else w}"*' for w in words]
        # значения — параметрами ?, склеиваются только постоянные части
        q = ("SELECT e.* FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid "  # noqa: S608
             "WHERE entries_fts MATCH ?" + (" AND e.kind = ?" if kind else "") +
             " ORDER BY bm25(entries_fts) - CASE WHEN e.owner = ? THEN 3 ELSE 0 END LIMIT ?")
        for fts in ([" ".join(terms), " OR ".join(terms)] if len(terms) > 1 else [" ".join(terms)]):
            if len(rows) >= limit or not terms:
                break
            seen = {r["id"] for r in rows}
            args: list = [fts] + ([kind] if kind else []) + [GLOBAL, limit * 3]
            rows += [r for r in self.db.execute(q, args).fetchall() if r["id"] not in seen][: limit - len(rows)]
        return rows

    def members(self, type_name: str) -> list[sqlite3.Row]:
        t = type_name.strip()
        rows = self.db.execute(
            "SELECT * FROM entries WHERE kind IN ('method','property','event') AND "
            "(pylower(owner) = ? OR pylower(owner_en) = ?) ORDER BY kind, name", (t.lower(), t.lower())).fetchall()
        return rows + self.db.execute("SELECT * FROM entries WHERE kind = 'ctor' AND (name_lc = ? OR name_en_lc = ?)",
                                      (t.lower(), t.lower())).fetchall()

    def check(self, name: str, compat: str | None = None, scope: str = "any") -> dict:
        """Есть ли имя в справке и с какой версии доступно.

        scope: 'any' — любое имя; 'global' — метод/свойство глобального контекста; 'method' — метод любого типа;
        'new' — тип, создаваемый конструктором `Новый`.
        """
        if scope == "global":
            rows = [r for r in self.lookup(name, owner=GLOBAL) if r["kind"] in ("method", "property")]
        elif scope == "method":
            rows = self.lookup(name, kind="method")
        elif scope == "new":
            rows = self.lookup(name, kind="ctor") or self.lookup(name, kind="type")
        else:
            rows = self.lookup(name)
        res = {"name": name, "platform": self.version, "found": bool(rows), "count": len(rows),
               "examples": [r["title"] for r in rows[:3]]}
        if scope == "new" and rows and rows[0]["kind"] == "type":
            res["no_ctor"] = True  # тип есть, но конструктора `Новый` у него нет
        keys = [r["since_key"] for r in rows if r["since_key"]]
        if keys:
            k = min(keys)  # самый ранний вариант (конструктор, одноимённый метод другого типа)
            res["since"] = next(r["since"] for r in rows if r["since_key"] == k)
            if compat and version_key(compat) and k > version_key(compat):
                res["newer_than_compat"] = version_str(compat)
        return res

    def meta(self) -> dict:
        return dict(self.db.execute("SELECT key, value FROM meta").fetchall())


_cache: dict[str, tuple[Index, list[str]]] = {}


def _line(v: str) -> tuple[int, ...]:
    return tuple(_vsort(v)[:2])


def build_command(bin_dir: str | None = None) -> str:
    return "otk platform-help build " + (f'--platform "{bin_dir}"' if bin_dir else
                                     "--platform <каталог bin> или --from <каталог с копией .hbk>")


def candidates(version: str | None) -> list[tuple[str, str | None, str]]:
    """Где искать справку версии, по порядку: [(версия, каталог .hbk или None — только индекс в кэше, примечание)].

    version — точная ('8.3.27.2342') или префикс ('8.3.27', '8.5', 'Version8_3_21'); пусто — новейшая."""
    plats, cached = installed_platforms(), cached_indexes()
    if not version:
        latest: list[tuple[str, str | None, str]] = [(v, plats[v], "") for v in reversed(plats)]
        return latest + [(v, None, "") for v in reversed(cached) if v not in plats]
    want = version_str(version) if version.startswith("Version") else version

    def mine(v):
        return v == want or v.startswith(want + ".")
    out: list[tuple[str, str | None, str]] = [(v, plats[v], "") for v in reversed(plats) if mine(v)]
    out += [(v, None, "") for v in reversed(cached) if v not in plats and mine(v)]
    key = version_key(want)
    near = sorted({v for v in [*plats, *cached] if _line(v) == _line(want) and not mine(v)},
                  key=lambda v: (abs(version_key(v) - key), [-x for x in _vsort(v)]))
    out += [(v, plats.get(v), f"справка {v}, платформа проверки {want}: справки своей версии нет — "
                              f"{build_command()}") for v in near]
    return out


def _open(version: str, bin_dir: str | None) -> tuple[Index, list[str]]:
    """Индекс версии: строит или обновляет при helpAuto; иначе — прежний с предупреждением или ошибка с командой."""
    if version in _cache:
        return _cache[version]
    db_path = os.path.join(cache_dir(), f"{version}.db")
    meta = _read_meta(db_path)
    have = meta.get("schema") == SCHEMA
    notes = []
    if bin_dir and (not have or meta.get("sources") != sources_signature(bin_dir)):
        if not AUTO:
            if not have:
                raise RuntimeError(f"индекса справки {version} нет (helpAuto = false): {build_command(bin_dir)}")
            notes.append(f"индекс справки {version} устарел — справка платформы изменилась (helpAuto = false): "
                         f"{build_command(bin_dir)}")
        else:
            try:
                os.makedirs(cache_dir(), exist_ok=True)
                build(version, bin_dir, db_path)
            except Exception as e:  # noqa: BLE001 — кэш только для чтения, новый формат .hbk: берётся прежний индекс
                hint = f" (кэш: {cache_root()}, задать — OTK_CACHE)" if isinstance(e, OSError) else ""
                if not have:
                    raise RuntimeError(f"индекс справки {version} не построен: {e}{hint}") from e
                notes.append(f"индекс справки {version} не обновлён ({e}{hint}) — взят прежний")
    elif not have:
        raise RuntimeError(f"индекса справки {version} нет: {build_command()}")
    _cache[version] = (Index(version, db_path), notes)
    return _cache[version]


def locate(version: str | None = None) -> tuple[Index, list[str]]:
    """(индекс справки для версии, примечания: подмена версии, устаревший индекс, что не открылось)."""
    errors = []
    for v, bin_dir, note in candidates(version):
        try:
            ix, notes = _open(v, bin_dir)
        except Exception as e:  # noqa: BLE001 — следующий по порядку источник справки
            errors.append(str(e))
            continue
        return ix, [n for n in (note, *notes) if n] + (["не открылась справка: " + "; ".join(errors)] if errors else [])
    if errors:
        raise RuntimeError("; ".join(errors))
    plats = installed_platforms()
    where = (f"установлены только другие линейки: {', '.join(plats)}" if plats else
             f"нет ни установленной платформы с {SOURCES['cntx']}, ни индекса в кэше")
    raise RuntimeError(f"справки платформы{' ' + version if version else ''} нет ({where}): "
                       f"{build_command()} либо environment.helpDir")


def open_index(version: str | None = None) -> Index:
    return locate(version)[0]


def open_exact(version: str, bin_dir: str | None = None) -> Index:
    """Индекс ровно этой версии (без подмены); нет — RuntimeError."""
    return _open(version, bin_dir or installed_platforms().get(version))[0]


def build_from(source_dir: str, version: str | None = None) -> tuple[str, str, int]:
    """otk platform-help build: индекс из каталога bin платформы или каталога с копией .hbk; версия — из имени каталога
    (…/8.3.27.2342/bin, …/8.3.27.2342) или явно. -> (версия, файл индекса, записей)."""
    src = os.path.abspath(source_dir)
    if not os.path.exists(os.path.join(src, SOURCES["cntx"])):
        raise RuntimeError(f"в {src} нет {SOURCES['cntx']}")
    version = version or version_of_dir(src)
    if len(_vsort(version or "")) < 3:
        raise RuntimeError(f"версия платформы по имени каталога {src} не определяется — укажите --version 8.3.27.2342")
    os.makedirs(cache_dir(), exist_ok=True)
    db_path = os.path.join(cache_dir(), f"{version}.db")
    cached = _cache.pop(version, None)
    if cached:
        cached[0].db.close()  # иначе Windows не даст заменить файл
    return version, db_path, build(version, src, db_path)


def status() -> list[dict]:
    """Справка по версиям (help status, doctor): {version, source, index: актуален | устарел | нет | без платформы}."""
    plats, cached = installed_platforms(), cached_indexes()
    rows = []
    for v in sorted({*plats, *cached}, key=_vsort):
        meta, src = cached.get(v), plats.get(v)
        state = ("нет" if not meta else "без платформы" if not src else
                 "актуален" if meta.get("sources") == sources_signature(src) else "устарел")
        rows.append({"version": v, "source": src or (meta or {}).get("bin", ""), "index": state,
                     "entries": int(meta["entries"]) if meta else 0})
    return rows
