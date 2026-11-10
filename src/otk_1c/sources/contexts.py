"""Контексты компиляции строки модуля: где Конфигуратор компилирует этот код (6.13).

Контекст строки = контексты модуля ∩ директива метода ∩ ветки препроцессора. Правила сверены с Конфигуратором
8.5.1 (/CheckModules по режимам на временном расширении копий двух отраслевых конфигураций):
- общий модуль — по флагам (клиент управляемого приложения = тонкий и веб-клиент) ∩ директива метода
  (&НаСервере в клиент-серверном модуле на клиенте не компилируется, &НаКлиенте — на сервере);
- модуль формы — по директиве метода, метод без директивы компилируется только на сервере;
- модуль объекта, менеджера, набора записей, менеджера значения — сервер и внешнее соединение;
- `#Если Сервер` истинно и во внешнем соединении; `#Если Клиент` — в тонком и веб-клиенте.
Толстый клиент и мобильные контексты не рассматриваются. None — контекст не определить (проверка пропускается).

Доступность методов и свойств глобального контекста — таблица фактической доступности по Конфигуратору
(availability/<версия>.json, генератор — otk availability build): справка с компилятором расходится.
"""
import bisect
import json
import re
from functools import lru_cache
from pathlib import Path

from ..lang import lexicon
from ..paths import cache_root

SERVER, THIN, WEB, EXTERNAL = "Server", "ThinClient", "WebClient", "ExternalConnection"
SERVER, THIN, WEB, EXTERNAL = "Server", "ThinClient", "WebClient", "ExternalConnection"
ORDINARY = "OrdinaryClient"
# контексты плана (schema/plan.schema.json): контексты модуля, environment.contexts и environment.requiredContexts
PLAN_CONTEXTS = (SERVER, THIN, ORDINARY, WEB, EXTERNAL)
NAMES = {SERVER: "сервер", THIN: "тонкий клиент", WEB: "веб-клиент", EXTERNAL: "внешнее соединение"}
# символы препроцессора (канонические, из словаря), истинные в контексте
_TRUE = {SERVER: {"Server"}, EXTERNAL: {"Server", "ExternalConnection"},
         THIN: {"Client", "ThinClient"}, WEB: {"Client", "WebClient"}}
_CLIENT = frozenset({THIN, WEB})
_DIRECTIVE = {"Client": _CLIENT, "Server": frozenset({SERVER, EXTERNAL}),
              "ServerNoContext": frozenset({SERVER, EXTERNAL}),
              "ClientServer": frozenset({SERVER, THIN, WEB, EXTERNAL}),
              "ClientServerNoContext": frozenset({SERVER, THIN, WEB, EXTERNAL})}
_FORM_DEFAULT = frozenset({SERVER})             # метод формы без директивы
_OBJECT = frozenset({SERVER, EXTERNAL})
_MODULE = {"ObjectModule": _OBJECT, "ManagerModule": _OBJECT, "RecordSetModule": _OBJECT,
           "ValueManagerModule": _OBJECT, "SessionModule": frozenset({SERVER}),
           "ManagedApplicationModule": _CLIENT, "ExternalConnectionModule": frozenset({EXTERNAL})}
_TOKEN = re.compile(r"[\wА-Яа-яЁё]+|[()]")


def _common(entry):
    out = set()
    for c in entry.get("contexts", []):
        out |= {SERVER} if c == "Server" else _CLIENT if c == "ThinClient" else {EXTERNAL} if c == EXTERNAL else set()
    return frozenset(out)


def evaluate(condition, ctx):
    """Условие #Если в контексте: True/False или None (не разобрать)."""
    lx = lexicon()
    words = {"not": {w.lower() for w in lx.preprocessor["not"]}, "and": {"и", "and"}, "or": {"или", "or"}}
    out = []
    for tok in _TOKEN.findall(condition):
        low = tok.lower()
        if tok in "()":
            out.append(tok)
        elif low in words["not"]:
            out.append("not")
        elif low in words["and"]:
            out.append("and")
        elif low in words["or"]:
            out.append("or")
        elif low in lx.preprocessor_symbols:
            out.append(lx.preprocessor_symbols[low] in _TRUE[ctx])
        else:
            return None
    return _logic(out) if out else True


def _logic(tokens):
    """Значение выражения из True/False, not, and, or и скобок (приоритет: not, and, or) или None — не разобрать."""
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def operand():
        nonlocal pos
        tok = peek()
        pos += 1
        if tok == "not":
            return not operand()
        if tok == "(":
            value = either()
            if peek() != ")":
                raise ValueError("нет закрывающей скобки")
            pos += 1
            return value
        if isinstance(tok, bool):
            return tok
        raise ValueError(f"ожидался операнд: {tok}")

    def both():
        nonlocal pos
        value = operand()
        while peek() == "and":
            pos += 1
            value = operand() and value
        return value

    def either():
        nonlocal pos
        value = both()
        while peek() == "or":
            pos += 1
            value = both() or value
        return value

    try:
        value = either()
    except ValueError:
        return None
    return value if pos == len(tokens) else None


def line_contexts(entry, parsed):
    """Функция: номер строки -> frozenset контекстов (из SERVER, THIN, WEB, EXTERNAL) или None."""
    kind = entry.get("kind")
    starts = [m.start for m in parsed.methods]      # методы — по порядку текста
    cache = {}

    def method_at(no):
        i = bisect.bisect_right(starts, no) - 1
        return parsed.methods[i] if i >= 0 and no <= parsed.methods[i].end else None

    def base(method):
        if kind == "CommonModule":
            ctx = _common(entry)
            if method and method.directive:
                return ctx & _DIRECTIVE[method.directive] if method.directive in _DIRECTIVE else None
            return ctx
        if kind == "FormModule":
            if method is None:
                return None
            if not method.directive:
                return _FORM_DEFAULT
            return _DIRECTIVE[method.directive] & {SERVER, THIN, WEB} if method.directive in _DIRECTIVE else None
        if kind == "CommandModule":
            if method and method.directive in _DIRECTIVE:
                return _DIRECTIVE[method.directive] & {SERVER, THIN, WEB}
            return None
        if kind in _MODULE:
            return None if method and method.directive else _MODULE[kind]
        return None

    def at(no):
        method = method_at(no)
        conds = parsed.conditions[no] if no < len(parsed.conditions) else ()
        key = (id(method), conds)
        if key not in cache:        # модуль в 30 тыс. строк: контекст считается на метод и набор условий
            cache[key] = _at(method, conds)
        return cache[key]

    def _at(method, conds):
        ctx = base(method)
        if not ctx:
            return ctx
        keep = set()
        for c in ctx:
            values = [evaluate(cond, c) for cond in conds]
            if None in values:
                return None
            if all(values):
                keep.add(c)
        return frozenset(keep)
    return at


_DATA = Path(__file__).parent.parent / "availability"


def user_tables_dir():
    """Таблицы, построенные у пользователя (otk availability build): важнее поставки той же версии."""
    return Path(cache_root()) / "availability"


def table_files():
    """{версия: файл таблицы}: поставка, поверх — таблицы пользователя."""
    files = {f.stem: f for f in _DATA.glob("*.json")}
    files.update({f.stem: f for f in user_tables_dir().glob("*.json")})
    return files


def nearest_table(version, files):
    """Версия таблицы для платформы проверки: та же, иначе ближайшая той же линейки (8.3, 8.5), иначе ближайшая."""
    if not files:
        return None
    want = version_key(version)
    same_line = [v for v in files if version_key(v)[:2] == want[:2]]
    pool = same_line or list(files)
    return min(pool, key=lambda v: (abs(version_key(v)[2] - want[2]) if same_line else 0,
                                    sum(abs(a - b) for a, b in zip(version_key(v), want, strict=False))))


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


@lru_cache(maxsize=None)
def types_web_measured(version):
    """Веб-клиент типов в таблице версии измерен в настоящем веб-клиенте, а не взят из справки."""
    files = table_files()
    best = nearest_table(version, files)
    return bool(best) and bool(_table_data(str(files[best])).get("typesWebClient"))


@lru_cache(maxsize=None)
def _table_data(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def availability_table(version):
    """{имя в нижнем регистре (рус. и англ.): ("method"|"property", frozenset контекстов)} для версии платформы
    проверки: таблица этой версии, иначе ближайшая той же линейки (8.3, 8.5), иначе ближайшая вообще; нет таблиц —
    пустой словарь (проверка по контексту не выполняется). Типы (`Новый Т`) — под ключом «new <имя>»
    с видом "type": имя типа может совпадать с именем метода; в таблице без раздела types их нет."""
    files = table_files()
    best = nearest_table(version, files)
    if not best:
        return {}
    data = _table_data(str(files[best]))
    out = {}
    for kind, section in (("method", "methods"), ("property", "properties")):
        for name, ctx in data[section].items():
            entry = (kind, frozenset(ctx))
            out[name.lower()] = entry
            en = data["english"].get(name)
            if en:
                out[en.lower()] = entry
    for name, ctx in data.get("types", {}).items():
        entry = ("type", frozenset(ctx))
        out[f"new {name.lower()}"] = entry
        en = data.get("typesEnglish", {}).get(name)
        if en:
            out[f"new {en.lower()}"] = entry
    return out
