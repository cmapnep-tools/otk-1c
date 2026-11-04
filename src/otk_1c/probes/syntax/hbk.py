"""Чтение справки синтакс-помощника 1С (*.hbk) и разбор её страниц.

Файл .hbk — контейнер платформы: оглавление документов, каждый документ — цепочка страниц
с заголовком `\\r\\n%08x %08x %08x \\r\\n` (размер документа, размер страницы, адрес следующей).
Документ `FileStorage` — ZIP со страницами HTML. Формат не документирован; схема разбора — по статье
https://infostart.ru/1c/articles/2804420/, проверена на 8.3.27.2342 и 8.5.1.1529.
"""
from __future__ import annotations

import html
import io
import re
import struct
import zipfile

_NEXT_NONE = 0x7FFFFFFF


def _read_doc(f, addr: int) -> bytes:
    f.seek(addr)
    h = f.read(31)
    size, page, nxt = int(h[2:10], 16), int(h[11:19], 16), int(h[20:28], 16)
    out, need = bytearray(), size
    take = min(page, need)
    out += f.read(take)
    need -= take
    while need > 0 and nxt != _NEXT_NONE:
        f.seek(nxt)
        h = f.read(31)
        page, nxt = int(h[11:19], 16), int(h[20:28], 16)
        take = min(page, need)
        out += f.read(take)
        need -= take
    return bytes(out)


def open_storage(path: str) -> zipfile.ZipFile:
    with open(path, "rb") as f:
        toc = _read_doc(f, 16)
        docs = {}
        for i in range(0, len(toc) - 11, 12):
            head_addr, body_addr, _ = struct.unpack("<iii", toc[i:i + 12])
            name = _read_doc(f, head_addr)[20:].decode("utf-16le", "replace").rstrip("\x00")
            docs[name] = body_addr
        data = _read_doc(f, docs["FileStorage"])
    return zipfile.ZipFile(io.BytesIO(data))


_CHAPTER = re.compile(r'<p class="V8SH_chapter">(.*?)</p>', re.S)
_TITLE = re.compile(r'<h1 class="V8SH_pagetitle">(.*?)</h1>', re.S)
_VERSION_INFO = re.compile(r'<p class="V8SH_versionInfo">(.*?)</p>', re.S)
_SINCE = re.compile(r"начиная с версии\s+(\d+(?:\.\d+){1,3})")
_KIND = re.compile(r"/(methods|properties|events|ctors)/(?:[^/]+/)*[^/]+\.html$")


def text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|(?=<div)", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html.unescape(fragment).replace("\xa0", " ")
    return re.sub(r"\n\s*\n+", "\n", fragment).strip()


def split_title(title: str) -> tuple[str, str]:
    """«Глобальный контекст.СтрНайти (Global context.StrFind)» -> (рус., англ.)."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", title)
    return (m.group(1).strip(), m.group(2).strip()) if m else (title.strip(), "")


def parse_page(page: str) -> dict | None:
    m = _TITLE.search(page)
    if not m:
        return None
    parts = _CHAPTER.split(page)
    chapters: dict[str, list[str]] = {}
    for i in range(1, len(parts) - 1, 2):
        chapters.setdefault(text(parts[i]).rstrip(": "), []).append(text(parts[i + 1]))
    versions = [text(v) for v in _VERSION_INFO.findall(page)]
    since = next((s.group(1) for v in versions if (s := _SINCE.search(v))), "")
    if not since:  # у конструкторов строка версии бывает только в шапке страницы
        s = _SINCE.search(text(page[:m.end() + 2000]))
        since = s.group(1) if s else ""

    def ch(*names):
        return "\n".join(x for n in names for x in chapters.get(n, []))

    owner = re.search(r'<p class="V8SH_title">(.*?)</p>', page, re.S)
    return {
        "title": text(m.group(1)),
        "owner_title": text(owner.group(1)) if owner else "",
        "syntax": ch("Синтаксис"),
        "params": ch("Параметры"),
        "returns": ch("Возвращаемое значение", "Тип значения"),
        "description": ch("Описание"),
        "availability": ch("Доступность"),
        "note": ch("Примечание"),
        "since": since,
        "versions": "\n".join(versions),
        "body": text(page[m.end():]),
    }


_PROSE_TITLE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def parse_prose(page: str) -> dict | None:
    """Страница справки языка или запросов (свёрстана иначе): заголовок и текст целиком."""
    m = _PROSE_TITLE.search(page)
    if not m:
        return None
    body = re.sub(r"(?is)<(script|style|head).*?</\1>", "", page[m.end():])
    s = _SINCE.search(text(body))
    return {"title": text(m.group(1)), "owner_title": "", "syntax": "", "params": "", "returns": "",
            "description": "", "availability": "", "note": "", "since": s.group(1) if s else "",
            "versions": "", "body": text(body)}


def kind_of(name: str, source: str) -> str | None:
    """Вид записи по пути в архиве; None — служебная страница (оглавление каталога)."""
    if source != "cntx":
        return source
    m = _KIND.search(name)
    if m:
        return {"methods": "method", "properties": "property", "events": "event", "ctors": "ctor"}[m.group(1)]
    if name.startswith("objects/") and name.endswith(".html") and not re.search(r"/catalog\d+\.html$", name):
        return "type"
    return None
