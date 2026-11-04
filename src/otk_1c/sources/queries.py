"""Извлечение текстов запросов из строковых литералов модуля.

Группа — литералы, соединённые только `+`. Группа полная (`complete`), если с обеих сторон к ней
не приклеено ничего, кроме литералов.

Собранный текст (6.6): переменная часть (`"... ГДЕ " + Условие + " ..."`) и заглушка шаблона
(`СтрЗаменить(Текст, "#Условие", ...)`) восстанавливаются, если это возможно без исполнения:
- значение известно из кода метода — литерал или переменная, которой присвоен только литерал;
- иначе нейтральная подстановка по месту в тексте: условие — `ИСТИНА`, значение — `NULL`, поле списка
  выбора — `NULL КАК <имя>`, имя временной таблицы — своё имя.
Имя таблицы или поля (`ИЗ <?>`, `Справочник.<?>`), поля упорядочивания и всё, что не распознано, не
подставляется: запрос остаётся неполным с причиной (`reason`). Восстановленный запрос — `assembled`:
ошибка его разбора — предупреждение (подстановка может не совпасть с тем, что соберёт код).
Заглушка `&Имя` без известного значения остаётся параметром — так её разбирает и платформа.
"""
import re

from ..lang import lexicon

_W = r"[\wА-Яа-яЁё]"
_TOKEN = re.compile(rf"{_W}+|[^\s\w]")
# заглушки шаблонов: &Имя (параметр, если не заменяется), #Имя, #Имя#, %Имя%, %1 (СтрШаблон), [Имя], {0}, <Имя>;
# шаблоны ограничения доступа БСП: @Справочник.Имя, ?.Поле
_PLACEHOLDER = re.compile(rf"&{_W}+|#{_W}+#?|%{_W}+%|%\d+|\[{_W}+\]|\{{\d+\}}|<{_W}+>|@{_W}+(?:\.{_W}+)*|\?(?=\.)")
_ASSIGN = re.compile(rf"(?:^|;|(?<!{_W})(?:Тогда|Иначе|Цикл|Попытка|Исключение|Then|Else|Do|Try|Except))"
                     rf"\s*({_W}+)\s*=\s*([^;]*);", re.M | re.I)
REPLACE = re.compile(r"(?<![\w.])(?:СтрЗаменить|StrReplace)\s*\(", re.I)
TEMPLATE = re.compile(r"(?<![\w.])(?:СтрШаблон|StrTemplate|(?:СтроковыеФункцииКлиентСервер|СтроковыеФункции|"
                      r"СтроковыеФункцииКлиент|СтроковыеФункцииСервер)\.ПодставитьПараметрыВСтроку)\s*\(", re.I)
_TARGET = re.compile(rf"({_W}+(?:\.{_W}+)*)\s*=\s*$")

TABLE_BEFORE = {"ИЗ", "FROM", "СОЕДИНЕНИЕ", "JOIN"}
COND_BEFORE = {"ГДЕ", "WHERE", "И", "AND", "ИЛИ", "OR", "НЕ", "NOT", "ИМЕЮЩИЕ", "HAVING", "КОГДА", "WHEN"}
VALUE_BEFORE = {"ТОГДА", "THEN", "ИНАЧЕ", "ELSE", "=", "<", ">", "+", "-", "*", "/", "(", ",", "В", "IN"}
FIELD_LIST_BY = {"УПОРЯДОЧИТЬ", "ORDER", "СГРУППИРОВАТЬ", "GROUP", "ИТОГИ", "TOTALS", "ИНДЕКСИРОВАТЬ", "INDEX"}
SELECT_HEAD = {"ВЫБРАТЬ", "SELECT", "РАЗЛИЧНЫЕ", "DISTINCT", "РАЗРЕШЕННЫЕ", "ALLOWED"}
SELECT_END = {"ИЗ", "FROM", "ПОМЕСТИТЬ", "INTO", ",", ";"}
AS = {"КАК", "AS"}
FRAGMENT_END = {"ИЗ", "FROM", "ГДЕ", "WHERE", "И", "AND", "ИЛИ", "OR", "(", ",", "СОЕДИНЕНИЕ", "JOIN", "ПО", "BY",
                "КАК", "AS", "ВЫБРАТЬ", "SELECT", "=", "ТОГДА", "THEN", "ИНАЧЕ", "ELSE", "ОБЪЕДИНИТЬ", "UNION",
                "ВСЕ", "ALL"}
INTO = {"ПОМЕСТИТЬ", "INTO"}
QUERY_KEYWORDS = (TABLE_BEFORE | COND_BEFORE | VALUE_BEFORE | FIELD_LIST_BY | SELECT_HEAD | SELECT_END | AS
                  | FRAGMENT_END | {"ПЕРВЫЕ", "TOP", "ДЛЯ", "FOR", "ИЗМЕНЕНИЯ", "UPDATE", "ЛЕВОЕ", "LEFT", "ПРАВОЕ",
                                    "RIGHT", "ВНУТРЕННЕЕ", "INNER", "ПОЛНОЕ", "FULL", "ИСТИНА", "TRUE",
                                    "ЛОЖЬ", "FALSE"})

TABLE_REASON = "имя таблицы или поля собирается в коде"
UNKNOWN_REASON = "текст собран с переменными частями"


def _query_re():
    """Текст запроса: начинается с ВЫБРАТЬ/SELECT и содержит хотя бы одно слово запроса (ИЗ, КАК...)."""
    lx = lexicon()
    start = "|".join(lx.query_start)
    words = "|".join(lx.query_words)
    return re.compile(rf"^\s*(?:{start})\s(?=.*(?<![\wА-Яа-яЁё])(?:{words})(?![\wА-Яа-яЁё]))", re.I | re.S)


_QUERY = None


def _tokens(text):
    return [t.upper() for t in _TOKEN.findall(text)]


def _in_select_list(before):
    """Место — список выбора: после последнего ВЫБРАТЬ нет ИЗ/ПОМЕСТИТЬ и открытых скобок."""
    toks = _tokens(before)
    last = max((i for i, t in enumerate(toks) if t in ("ВЫБРАТЬ", "SELECT")), default=None)
    if last is None:
        return False
    depth = 0
    for t in toks[last + 1:]:
        if t in ("ИЗ", "FROM", "ПОМЕСТИТЬ", "INTO"):
            return False
        depth += (t == "(") - (t == ")")
    return depth == 0


def neutral(before, after, n=1):
    """Нейтральная подстановка вместо неизвестной части по тексту до и после неё: (текст, None) или
    (None, причина)."""
    if before.rstrip().endswith(".") or after.lstrip().startswith("."):
        return None, TABLE_REASON
    b, a = _tokens(before), _tokens(after)
    last = b[-1] if b else ""
    nxt = a[0] if a else ""
    if last in TABLE_BEFORE or (last == "(" and len(b) > 1 and b[-2] in TABLE_BEFORE):
        return None, TABLE_REASON                 # таблица или вложенный запрос
    if last == "(" and len(b) > 1 and b[-2] in ("СТРОКА", "STRING", "ЧИСЛО", "NUMBER"):
        return None, UNKNOWN_REASON               # длина или точность типа
    if last in INTO:
        return f"ВТ_ОТК{n}", None
    if last in ("ПО", "BY"):
        if len(b) > 1 and b[-2] in FIELD_LIST_BY:
            return None, "поля упорядочивания или группировки собираются в коде"
        return "ИСТИНА", None                     # условие соединения
    if last in COND_BEFORE:
        return "ИСТИНА", None
    if _in_select_list(before) and (last in SELECT_HEAD or last == "," or last.isdigit()):
        if nxt in AS:
            return "NULL", None
        if not nxt or nxt in SELECT_END:
            return f"NULL КАК ОТКПоле{n}", None
        if last in SELECT_HEAD or last.isdigit():
            return "", None                       # модификатор выборки (ПЕРВЫЕ 10, РАЗРЕШЕННЫЕ) перед полями
        return None, UNKNOWN_REASON
    if last in VALUE_BEFORE:
        return "NULL", None
    return None, UNKNOWN_REASON


def _literal_value(module, start, end):
    """Значение выражения из одних литералов и `+` в [start, end) или None."""
    lits = [lit for lit in module.literals if start <= lit.start and lit.end <= end]
    if not lits:
        return None
    rest = module.sanitized[start:end]
    for lit in reversed(lits):
        rest = rest[:lit.start - start] + rest[lit.end - start:]
    return "".join(lit.value for lit in lits) if set(rest) <= set(" \t\r\n+") else None


def _offsets(module):
    starts = [0]
    for i, c in enumerate(module.text):
        if c == "\n":
            starts.append(i + 1)
    return starts


class _Scope:
    """Значения в пределах метода запроса: переменные с единственным литеральным присваиванием и
    заглушки СтрЗаменить(Текст, "<заглушка>", <значение>)."""

    def __init__(self, module, line, starts):
        self.module = module
        m = module.method_at(line)
        self.lo = starts[m.start - 1] if m else 0
        self.hi = starts[m.end] if m and m.end < len(starts) else len(module.text)
        self.s = module.sanitized
        self._vars = None
        self._placeholders = None

    def variable(self, name):
        if self._vars is None:
            values, seen = {}, {}
            for mm in _ASSIGN.finditer(self.s, self.lo, self.hi):
                key = mm.group(1).lower()
                seen[key] = seen.get(key, 0) + 1
                values[key] = _literal_value(self.module, mm.start(2), mm.end(2))
            self._vars = {k: v for k, v in values.items() if seen[k] == 1 and v is not None}
        return self._vars.get(name.lower())

    def expression(self, start, end):
        """Значение выражения переменной части (текст между литералами без крайних `+`)."""
        value = _literal_value(self.module, start, end)
        if value is not None:
            return value
        code = self.s[start:end].strip().strip("+").strip()
        return self.variable(code) if re.fullmatch(rf"{_W}+", code) else None

    def placeholders(self):
        """{заглушка в нижнем регистре: значение или None}."""
        if self._placeholders is None:
            self._placeholders = {}
            for mm in REPLACE.finditer(self.s, self.lo, self.hi):
                args = self._args(mm.end())
                if len(args) != 3:
                    continue
                ph = _literal_value(self.module, *args[1])
                if not ph or not (_PLACEHOLDER.fullmatch(ph) or (re.fullmatch(rf"{_W}{{3,}}", ph)
                                                                  and ph.upper() not in QUERY_KEYWORDS)):
                    continue                      # ключ — заглушка или слово, но не слово языка запросов
                self._put(ph, self.expression(*args[2]))
            for mm in TEMPLATE.finditer(self.s, self.lo, self.hi):
                for n, arg in enumerate(self._args(mm.end())[1:], 1):       # %1, %2… — аргументы по порядку
                    self._put(f"%{n}", self.expression(*arg))
        return self._placeholders

    def _put(self, ph, value):
        key = ph.lower()
        if key in self._placeholders and self._placeholders[key] != value:
            value = None                          # разные значения в разных ветках — подставлять нечего
        self._placeholders[key] = value

    def appended(self, target, after):
        """Текст в переменной target дописывается дальше в методе (target = target + ...)."""
        n = re.escape(target)
        return re.search(rf"(?<![\w.]){n}\s*=\s*{n}\s*\+", self.s[after:self.hi], re.I) is not None

    def _args(self, pos):
        """Границы аргументов вызова от позиции после `(` до парной `)`."""
        args, depth, start = [], 0, pos
        for i in range(pos, self.hi):
            c = self.s[i]
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    args.append((start, i))
                    return args
                depth -= 1
            elif c == "," and depth == 0:
                args.append((start, i))
                start = i + 1
            elif c == ";":
                break
        return []


def _assemble(parts, scope):
    """parts: строки и (start, end) переменных частей. Возвращает (текст, причина, подставлено)."""
    out, n, changed = "", 1, False
    for i, p in enumerate(parts):
        if isinstance(p, str):
            out += p
            continue
        value = scope.expression(*p)
        if value is None:
            after = next((q for q in parts[i + 1:] if isinstance(q, str)), "")
            value, why = neutral(out, after, n)
            if value is None:
                return None, why, changed
            n += 1
        out += value
        changed = True
    return out, None, changed


def _fill_placeholders(text, scope):
    """Заглушки шаблона: известные — значением; `#Имя` без значения — нейтрально по месту; `&Имя` без
    значения — остаётся параметром. Возвращает (текст, причина, подставлено)."""
    known = scope.placeholders()
    # заглушка-слово (СтрЗаменить(Текст, "ИмяТаблицы", …)) — целым словом, не после точки
    words = [k for k in known if re.fullmatch(rf"{_W}+", k)]
    marker = (re.compile(rf"{_PLACEHOLDER.pattern}|(?<![\w.&#@])(?:{'|'.join(map(re.escape, words))})(?![\w])", re.I)
              if words else _PLACEHOLDER)
    changed, n, pos = False, 1, 0
    while True:
        mm = marker.search(text, pos)
        if not mm:
            return text, None, changed
        ph = mm.group(0)
        if text.count('"', 0, mm.start()) % 2:      # внутри строки самого запроса (ПОДОБНО "%#%")
            pos = mm.end()
            continue
        value = known.get(ph.lower())
        if value is None and ph.startswith("#") and ph.endswith("#"):
            value = known.get(ph[:-1].lower())
        if value is None:
            head = text[:mm.start()].rstrip()
            # параметр-таблица допустим после ИЗ, но не после СОЕДИНЕНИЕ и не как часть имени (Документ.&Имя)
            # и не заглушка, которую код заменяет СтрЗаменить (значение неизвестно — это уже не параметр)
            if (ph.startswith("&") and ph.lower() not in known and not head.endswith(".")
                    and (_tokens(head[-20:]) or [""])[-1] not in ("СОЕДИНЕНИЕ", "JOIN")):
                pos = mm.end()
                continue
            value, why = neutral(text[:mm.start()], text[mm.end():], n)
            if value is None:
                return None, why, changed
            n += 1
        text = text[:mm.start()] + value + text[mm.end():]
        pos = mm.start() + len(value)
        changed = True


def extract(module, lines=None):
    """Запросы модуля: [{'line', 'text', 'complete', 'assembled', 'reason'}]. lines — множество строк,
    которым должен принадлежать хотя бы один литерал группы (None — весь модуль)."""
    global _QUERY
    if _QUERY is None:
        _QUERY = _query_re()
    s = module.sanitized
    # группа: литералы одного выражения; между ними либо '+', либо '+ <выражение> +' (переменная часть)
    groups, cur = [], []
    for lit in module.literals:
        between = s[cur[-1].end:lit.start].strip() if cur else ""
        if cur and between == "+":
            cur.append(lit)
        elif cur and len(between) > 1 and between[0] == "+" and between[-1] == "+" and ";" not in between:
            cur.append((cur[-1].end, lit.start))       # переменная часть
            cur.append(lit)
        else:
            if cur:
                groups.append(cur)
            cur = [lit]
    if cur:
        groups.append(cur)
    result = []
    starts = None
    for g in groups:
        lits = [lit for lit in g if not isinstance(lit, tuple)]
        text = "".join("<?>" if isinstance(lit, tuple) else lit.value for lit in g)
        if not _QUERY.search(text) or "`" in text:     # обратные кавычки — SQL внешней базы, не запрос 1С
            continue
        if lines is not None and not any(lit.line in lines for lit in lits):
            continue
        before = s[:lits[0].start].rstrip()
        after = s[lits[-1].end:].lstrip()
        q = {"line": lits[0].line, "text": text, "complete": True, "assembled": False, "reason": None}
        if before.endswith("+") or after.startswith("+"):
            q.update(complete=False, reason=UNKNOWN_REASON)   # текст дописывается к переменной или переменная к нему
            result.append(q)
            continue
        if starts is None:
            starts = _offsets(module)
        scope = _Scope(module, lits[0].line, starts)
        target = _TARGET.search(before[-200:])
        if target and scope.appended(target.group(1), lits[-1].end):
            q.update(complete=False, reason="текст дописывается в коде дальше")
            result.append(q)
            continue
        full, why, changed = _assemble([lit if isinstance(lit, tuple) else lit.value for lit in g], scope)
        if full is not None:
            full, why, filled = _fill_placeholders(full, scope)
            changed = changed or filled
        if full is not None and (_tokens(full[-30:]) or [""])[-1] in FRAGMENT_END:
            full, why = None, "фрагмент: текст продолжается в коде"
        if full is None:
            q.update(complete=False, reason=why)
        elif changed:
            q.update(text=full, assembled=True)
        result.append(q)
    return result
