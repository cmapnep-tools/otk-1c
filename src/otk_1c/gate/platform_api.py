"""Методы и типы платформы в изменённых строках (спецификация static-checks).

Источник — справка синтакс-помощника той платформы, которой идёт проверка (`otk_1c.syntax`: индекс
`shcntx_ru.hbk` установленной платформы с версией появления каждого элемента). Нет справки — шаг
пропускается с пояснением.

Что ловит (проверено Конфигуратором 8.5.1, `/CheckModules -Server`, расширение и основная
конфигурация в режиме совместимости 8.3.21):
- вызов без точки, которого нет ни в модуле, ни среди методов платформы — «Процедура или функция с
  указанным именем не определена»;
- метод глобального контекста из более новой линейки платформы, чем режим совместимости (8.5.1 при
  8.3.21), — та же ошибка;
- `Новый Тип` для типа без конструктора — «Конструктор не найден»;
- метод той же линейки новее режима совместимости (8.3.25 при 8.3.21) и конструктор любой версии
  (8.5.1 при 8.3.21) Конфигуратор пропускает, но на платформе ниже версии появления модуль не
  скомпилируется — предупреждение.
"""
import re
import tomllib
from pathlib import Path

from ..lang import lexicon
from ..probes import syntax
from ..sources import contexts, layout

_N = r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*"
_CALL = re.compile(rf"(?<![\wА-Яа-яЁё.&#~])({_N})\s*\(")
_NEW = re.compile(rf"(?<![\wА-Яа-яЁё.])(?:Новый|New)\s+({_N})", re.I)
_IDENT = re.compile(rf"(?<![\wА-Яа-яЁё.&#~])({_N})(?![\wА-Яа-яЁё])")
KEYWORDS = {
    "если", "иначеесли", "тогда", "иначе", "пока", "для", "каждого", "из", "по", "цикл", "возврат", "новый", "не",
    "и", "или", "выполнить", "вызватьисключение", "ждать", "знач", "процедура", "функция", "асинх", "перейти",
    "добавитьобработчик", "удалитьобработчик", "попытка", "исключение",
    "if", "elsif", "then", "else", "while", "for", "each", "in", "to", "do", "return", "new", "not", "and", "or",
    "execute", "raise", "await", "val", "procedure", "function", "async", "goto", "addhandler", "removehandler",
    "try", "except",
}
_DECL = re.compile(rf"{lexicon().alt('procedure', 'function')}\s*$", re.I)
# Имена платформы, которые статика знает без справки: встроенный словарь availability/platform-names.toml
# (methods — вызов без точки, constructors — «Новый Тип», globals — имя перед точкой, forms — стандартные формы,
# managerMethods — начало имени стандартного метода менеджера) плюс раздел [platformNames] настроек.
NAMES_FILE = Path(__file__).resolve().parent.parent / "availability" / "platform-names.toml"
NAMES_KEYS = ("methods", "constructors", "globals", "forms", "managerMethods")


def platform_names(extra=None):
    """Встроенный словарь + extra (раздел [platformNames] настроек): ключ -> множество имён в нижнем регистре.
    Неизвестный ключ или не список строк — ValueError с понятной причиной."""
    with open(NAMES_FILE, "rb") as f:
        base = tomllib.load(f)
    out = {k: {n.lower() for n in base.get(k, [])} for k in NAMES_KEYS}
    for key, names in (extra or {}).items():
        if key not in NAMES_KEYS:
            raise ValueError(f"[platformNames] {key}: неизвестный ключ, допустимы {', '.join(NAMES_KEYS)}")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"[platformNames] {key}: нужен список имён")
        out[key] |= {n.lower() for n in names}
    return out


_NEW_AT_END = re.compile(r"(?<![\wА-Яа-яЁё.])(?:Новый|New)\s*$", re.I)
# символы препроцессора веб-клиента (есть методы, которых нет в справке: ПаузаАсинх) и мобильной платформы
# (десктопный Конфигуратор такой код не компилирует)
_UNCHECKED = re.compile(r"веб|web|мобильн|mobile", re.I)
_NOT = re.compile(r"(?<![\wА-Яа-яЁё])(?:не|not)(?![\wА-Яа-яЁё])", re.I)


def _unchecked_only(cond):
    """Условие #Если выполняется только в веб-клиенте или на мобильной платформе: все символы такие, отрицания нет
    (#Иначе и «Не ВебКлиент» — код остальных клиентов, он проверяется)."""
    words = [w for w in re.findall(_N, cond) if w.lower() not in ("или", "or", "и", "and")]
    return bool(words) and not _NOT.search(cond) and all(_UNCHECKED.match(w) for w in words)


def open_help(platform_version, bin_dir=None, env=None):
    """(индекс справки платформы проверки, индексы остальных установленных платформ, примечания); bin_dir —
    платформа вне стандартных мест (environment.platform), env — [environment] (helpDir, helpAuto).

    Своей версии нет — ближайшая той же линейки с примечанием (syntax.locate).
    Справка неполна: в 8.3.27 нет ПолучитьЗаголовокКлиентскогоПриложения, а справка 8.5.1 даёт его
    «с 8.3.3». Имя, которого нет в своей справке, ищется в остальных — с версией не новее своей."""
    syntax.configure(env or {})
    syntax.add_platform(bin_dir)
    own, notes = syntax.locate(platform_version or None)
    others = []
    for v, b in syntax.installed_platforms().items():
        if v != own.version:
            try:
                others.append(syntax.open_exact(v, b))
            except Exception:  # noqa: BLE001, S110 — справка другой платформы лишь дополняет свою
                pass
    return own, others, notes


def compat_mode(configuration_xml, lay=layout.DESIGNER):
    """Режим совместимости, в котором компилируется код (у расширения — свой, у основной конфигурации — общий)."""
    return lay.properties(configuration_xml).compat


def _line_major(v):
    """'8.3.25' -> (8, 3): линейка платформы."""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:2]) if len(nums) >= 2 else None


def _prev_word(text, pos):
    return _DECL.search(text[max(0, pos - 40):pos])


def _member(lines, no, prefix):
    """Перед именем точка — на этой строке («Х. Метод(») или в конце предыдущей непустой
    («Отчеты.Х.» ⏎ «Метод(»): это член выражения, а не вызов без точки."""
    if prefix.strip():
        return prefix.rstrip().endswith(".")
    i = no - 2
    while i >= 0 and not lines[i].strip():
        i -= 1
    return i >= 0 and lines[i].rstrip().endswith(".")


class ApiChecker:
    def __init__(self, help_index, compat, others=(), web_client=False, min_platform=None, compat_warnings=True,
                 names=None):
        """web_client — конфигурация работает в веб-клиенте (environment.webClient): недоступное только
        в веб-клиенте — ошибка, а не предупреждение. min_platform (environment.minPlatform) и compat_warnings
        (environment.compatWarnings) — какие предупреждения «новее режима совместимости» выводить;
        скрытые считаются в hidden. names — имена без справки (platform_names()), по умолчанию —
        встроенный словарь."""
        self.names = names or platform_names()
        self.ix = help_index
        self.web_client = web_client
        self.others = list(others)
        self.compat = compat
        self.min_platform = str(min_platform) if min_platform else None
        self.compat_warnings = compat_warnings
        self.hidden = 0
        self._cache = {}
        self._own_key = self._vkey(help_index.version)

    def warns_newer(self, since):
        """Выводить ли предупреждение об элементе своей линейки с версии since (новее режима совместимости)."""
        if not self.compat_warnings:
            return False
        return not self.min_platform or self._vkey(since) > self._vkey(self.min_platform)

    def hidden_note(self):
        """Примечание сводки о скрытых предупреждениях или None."""
        if not self.hidden:
            return None
        why = ("environment.compatWarnings = false" if not self.compat_warnings
               else f"не новее environment.minPlatform {self.min_platform}")
        return f"предупреждений «новее режима совместимости» скрыто: {self.hidden} ({why})"

    @staticmethod
    def _vkey(v):
        nums = [int(x) for x in re.findall(r"\d+", v or "")[:3]] + [0, 0, 0]
        return tuple(nums[:3])

    def _check(self, name, scope):
        key = (name.lower(), scope)
        if key not in self._cache:
            res = self.ix.check(name, self.compat, scope)
            for other in self.others if not res["found"] else ():
                alt = other.check(name, self.compat, scope)
                if alt["found"] and alt.get("since") and self._vkey(alt["since"]) <= self._own_key:
                    res = alt
                    break
            self._cache[key] = res
        return self._cache[key]

    def availability(self, name, kind="method"):
        """Контексты, где имя глобального контекста доступно — по таблице фактической доступности Конфигуратора
        (contexts.availability_table; справка с компилятором расходится): множество из Server, ThinClient,
        WebClient, ExternalConnection или None (нет в таблице, нет таблиц)."""
        key = f"new {name.lower()}" if kind == "type" else name.lower()
        entry = contexts.availability_table(self.ix.version).get(key)
        # недоступно нигде — имя новее режима совместимости ИБ, на которой собрана таблица (БезопасноеХранилище):
        # о версии судит проверка «доступен с», здесь — не известно
        return set(entry[1]) if entry and entry[0] == kind and entry[1] else None

    def _contexts(self, name, line_ctx, no, emit, kind="method"):
        """Имя недоступно в контексте, где компилируется строка: Конфигуратор — «не определена» (6.13).
        Сервер, тонкий клиент, внешнее соединение — ошибка; только веб-клиент — предупреждение (gate веб-клиент
        не проверяет, полная проверка с -WebClient — да), с environment.webClient — ошибка."""
        if not line_ctx:
            return
        avail = self.availability(name, kind)
        if avail is None:
            return
        missing = set(line_ctx) - avail
        if not missing:
            return
        where = ", ".join(contexts.NAMES[c] for c in sorted(missing, key=list(contexts.NAMES).index))
        have = ", ".join(contexts.NAMES[c] for c in sorted(avail, key=list(contexts.NAMES).index)) or "нет"
        what, no_, yes, error = {"method": ("Метод", "недоступен", "доступен",
                                            "Процедура или функция с указанным именем не определена"),
                                 "property": ("Свойство", "недоступно", "доступно", "Переменная не определена"),
                                 "type": ("Тип", "недоступен", "доступен", "Тип не определен")}[kind]
        # тип только в веб-клиенте: по замеру в веб-клиенте — как метод (с environment.webClient — ошибка:
        # форма с таким модулем в веб-клиенте не открывается); по справке — всегда предупреждение (она ошибается:
        # ПараметрВыбора)
        web_warn = missing == {"WebClient"} and (
            not self.web_client or kind == "type" and not contexts.types_web_measured(self.ix.version))
        emit("platform-api", "warning" if web_warn else "error", no,
             f"{what} {name} {no_}: {where} ({yes}: {have}) — Конфигуратор: «{error}»")

    def _version(self, res, what, line, emit, gated=True):
        """gated — Конфигуратор отсекает элемент более новой линейки (методы глобального контекста);
        конструкторы типов он не отсекает ни в какой линейке (проверено: тип 8.5.1 при 8.3.21)."""
        since = res.get("since")
        if not since or not res.get("newer_than_compat"):
            return
        if gated and _line_major(since) > _line_major(self.compat):
            emit("platform-api", "error", line, f"{what} доступен с {since}: в режиме совместимости "
                 f"{res['newer_than_compat']} Конфигуратор его не определит")
        elif not self.warns_newer(since):
            self.hidden += 1
        else:
            emit("platform-api", "warning", line, f"{what} доступен с {since} (режим совместимости "
                 f"{res['newer_than_compat']}): на платформе ниже {since} модуль не скомпилируется")

    def _new(self, name, no, emit, line_ctx=None):
        """«Новый <name>»: тип и конструктор по справке, доступность в контексте строки — по таблице."""
        res = self._check(name, "new")
        if not res["found"]:
            emit("platform-api", "error", no, f"Тип не определен: Новый {name}")
        elif res.get("no_ctor") and name.lower() not in self.names["constructors"]:
            emit("platform-api", "error", no, f"Конструктор не найден: Новый {name}")
        else:
            self._version(res, f"Тип {name}", no, emit, gated=False)
            self._contexts(name, line_ctx, no, emit, "type")

    def _undeclared(self, ln, no, sanitized_lines, emit, names):
        """Имя перед точкой («Х.Метод(…)», «Х.Свойство»), которого нет ни в модуле, ни в конфигурации, ни в справке
        платформы: Конфигуратор — «Переменная не определена» (обращение к несуществующему общему модулю)."""
        seen = set()
        for m in _IDENT.finditer(ln):
            name = m.group(1)
            low = name.lower()
            if low in seen or not ln[m.end():].lstrip().startswith(".") or low in KEYWORDS or low in names or \
                    low in self.names["globals"] or _member(sanitized_lines, no, ln[:m.start()]) or \
                    re.search(r"(?:Новый|New)\s+$", ln[:m.start()], re.I):
                continue
            seen.add(low)
            if not self._check(name, "any")["found"]:
                emit("platform-api", "error", no, f"Переменная не определена: {name}")

    def check(self, kind, sanitized_lines, lines, emit, known, conditions=None, contexts=None, variables=None):
        """known — имена методов, доступных без точки помимо платформы (свои, основной конфигурации,
        глобальных общих модулей) и локальные переменные; conditions — условия #Если по строкам;
        contexts(no) — контексты компиляции строки (contexts.line_contexts) или None; variables — имена, известные
        перед точкой (переменные модуля, общие модули, менеджеры, глобальные переменные) или None — без проверки
        «Переменная не определена»."""
        scope = "global" if kind == "CommonModule" else "method"
        declared = known | variables if variables is not None else None     # один раз: на модуль в 30 тыс. строк
        for no in lines:
            ln = sanitized_lines[no - 1] if no - 1 < len(sanitized_lines) else ""
            if ln.lstrip().startswith(("#", "&")):
                continue    # директивы: «#Область Метаданные» — имя области, а не свойство (ERP)
            if conditions and no < len(conditions) and any(_unchecked_only(c) for c in conditions[no]):
                continue    # код только веб-клиента (ПаузаАсинх) или мобильной платформы (ОтправитьВложениеАндроид)
            # «Новый» в конце предыдущей строки: первое имя этой строки — тип конструктора (УНФ)
            prev = sanitized_lines[no - 2] if 1 < no <= len(sanitized_lines) + 1 else ""
            carried = _NEW_AT_END.search(prev) and re.match(rf"\s*({_N})", ln)
            line_ctx = contexts(no) if contexts is not None else None
            if carried:
                self._new(carried.group(1), no, emit, line_ctx)
            for m in _CALL.finditer(ln):
                name = m.group(1)
                low = name.lower()
                if low in KEYWORDS or low in known or low in self.names["methods"] or \
                        _prev_word(ln, m.start()) or _member(sanitized_lines, no, ln[:m.start()]):
                    continue
                if re.search(r"(?:Новый|New)\s+$", ln[:m.start()], re.I) or (carried and m.start() == carried.start(1)):
                    continue        # Новый Тип(...) — ниже / выше
                res = self._check(name, scope)
                if not res["found"]:
                    # не метод глобального контекста — но может быть методом объекта/формы в своём модуле
                    if scope == "global" or not self._check(name, "method")["found"]:
                        emit("platform-api", "error", no,
                             f"Процедура или функция с указанным именем не определена: {name}")
                    continue
                if scope == "global" or self._check(name, "global")["found"]:
                    self._version(self._check(name, "global"), f"Метод {name}", no, emit)
                    if contexts is not None:
                        self._contexts(name, contexts(no), no, emit)
            for m in _NEW.finditer(ln):
                self._new(m.group(1), no, emit, line_ctx)
            if declared is not None:
                self._undeclared(ln, no, sanitized_lines, emit, declared)
            if contexts is not None:
                # свойства глобального контекста (ПараметрыСеанса на клиенте — «Переменная не определена»)
                for m in _IDENT.finditer(ln):
                    name = m.group(1)
                    low = name.lower()
                    if low in KEYWORDS or low in known or ln[m.end():].lstrip().startswith("(") or \
                            _member(sanitized_lines, no, ln[:m.start()]):
                        continue
                    if self.availability(name, "property") is not None:
                        self._contexts(name, contexts(no), no, emit, "property")
