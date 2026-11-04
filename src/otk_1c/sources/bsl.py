"""Разбор модуля BSL до уровня методов.

Лексер отделяет код от строк и комментариев (многострочные строки с `|`, комментарии между строками
продолжения, удвоенные кавычки, даты в одинарных кавычках). Парсер находит методы, их сигнатуры,
директивы и аннотации, ветки препроцессора для каждой строки и строковые литералы.
Номера строк — с единицы.
"""
import bisect
import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..lang import lexicon


@dataclass
class Literal:
    start: int          # смещение открывающей кавычки
    end: int            # смещение после закрывающей кавычки
    line: int
    value: str          # значение строки 1С (без кавычек, "" -> ", строки продолжения через \n)


@dataclass
class Param:
    name: str
    by_value: bool
    has_default: bool


@dataclass
class Method:
    name: str
    kind: str                       # procedure | function
    export: bool
    params: list
    start: int                      # первая строка (с директивами и аннотациями)
    header: int                     # строка заголовка
    end: int                        # строка КонецПроцедуры/КонецФункции
    directive: str | None = None           # Server | Client | ... (из &НаСервере и т.п.)
    annotations: list = field(default_factory=list)   # ['Вместо("Имя")', ...]
    is_async: bool = False

    def signature(self):
        return (self.kind, self.export, tuple((p.name.lower(), p.by_value, p.has_default) for p in self.params))


@dataclass
class Module:
    text: str
    methods: list
    literals: list
    conditions: list                # для каждой строки (индекс = номер строки) — кортеж условий #Если
    code_lines: set                 # строки с кодом вне методов (тело модуля, объявления переменных)
    sanitized: str

    def method_at(self, line):
        for m in self.methods:
            if m.start <= line <= m.end:
                return m
        return None

    def by_name(self):
        return {m.name.lower(): m for m in self.methods}


def lex(text):  # noqa: C901 — лексер: один проход автомата по символам
    """Возвращает (sanitized, literals): sanitized — текст той же длины, где содержимое строк и
    комментариев заменено пробелами (кавычки строк сохранены), literals — строковые литералы."""
    out = list(text)
    literals = []
    n = len(text)
    i = 0
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line_of(pos):
        return bisect.bisect_right(line_starts, pos)

    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "'":
            j = text.find("'", i + 1)
            j = n - 1 if j < 0 else j
            for k in range(i + 1, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        elif c == '"':
            start = i
            unterminated = False
            parts, cur = [], []
            i += 1
            while i < n:
                ch = text[i]
                if ch == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        cur.append('"')
                        out[i] = out[i + 1] = " "
                        i += 2
                        continue
                    break
                if ch == "\n":
                    parts.append("".join(cur))
                    cur = []
                    i += 1
                    # строка продолжения: пробелы, затем | ; строки-комментарии пропускаются
                    while i < n:
                        k = i
                        while k < n and text[k] in " \t\r":
                            k += 1
                        if k < n and text[k] == "\n":
                            i = k + 1   # пустая строка внутри литерала: платформа её пропускает
                            continue
                        if text.startswith("//", k):
                            e = text.find("\n", k)
                            e = n if e < 0 else e
                            for p in range(i, e):
                                out[p] = " "
                            i = e + 1
                            continue
                        for p in range(i, k):
                            out[p] = " "
                        if k < n and text[k] == "|":
                            out[k] = " "
                            i = k + 1
                        else:
                            i = k   # незакрытая строка: обрываем на следующей строке кода
                            parts.append(None)
                        break
                    if parts and parts[-1] is None:
                        parts.pop()
                        unterminated = True
                        break
                    continue
                if ch != "\r":
                    cur.append(ch)
                out[i] = " "
                i += 1
            if not unterminated:
                parts.append("".join(cur))
            end = i if unterminated else min(i + 1, n)
            literals.append(Literal(start, end, line_of(start), "\n".join(parts)))
            i = end
        else:
            i += 1
    return "".join(out), literals


_NAME = r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*"


@lru_cache(maxsize=256)
def header_re(name=None, paren=True):
    """Заголовок метода с начала строки до открывающей скобки, все варианты языка, режим re.M: группы indent,
    async, kind, name. name — только метод с этим именем (без учёта регистра); paren=False — до конца имени.
    Строки и комментарии регулярка не различает: по тексту после `lex` (sanitized) или там, где это допустимо."""
    kw = lexicon().alt
    n = re.escape(name) if name else _NAME
    tail = r"[ \t]*\(" if paren else r"(?![\wА-Яа-яЁё])"
    return re.compile(rf"^(?P<indent>[ \t]*)(?:(?P<async>{kw('async')})[ \t]+)?"
                      rf"(?P<kind>{kw('procedure')}|{kw('function')})[ \t]+(?P<name>{n}){tail}", re.I | re.M)


@lru_cache(maxsize=None)
def end_re():
    """Строка конца метода (КонецПроцедуры/EndFunction…), режим re.M. Отступ — любые пробельные: в типовых
    встречается неразрывный пробел перед КонецПроцедуры (ERP, УНФ), платформа его принимает."""
    kw = lexicon().alt
    return re.compile(rf"^\s*(?:{kw('endProcedure')}|{kw('endFunction')})\b", re.I | re.M)


@lru_cache(maxsize=None)
def export_re():
    """Начало текста после закрывающей скобки заголовка — слово Экспорт/Export."""
    return re.compile(rf"^\s*(?:{lexicon().alt('export')})\b", re.I)


def declared(text):
    """Имена методов, объявленных в тексте (лучше — в sanitized: заголовки в строках и комментариях не в счёт).
    Регулярным выражением, а не разбором: заголовок бывает разделён #Если/#Иначе, разборщик его не видит."""
    return [m.group("name") for m in header_re(paren=False).finditer(text)]


def method_line(text, name):
    """Номер строки (с единицы) заголовка метода name или None."""
    m = header_re(name).search(text)
    return text.count("\n", 0, m.start()) + 1 if m else None


class Parser:
    def __init__(self):
        lx = lexicon()
        self.lx = lx
        kw = lx.alt
        self.re_header = header_re()
        self.re_end = end_re()
        self.re_end_inline = re.compile(rf"(?:^|[\s;])(?:{kw('endProcedure')}|{kw('endFunction')})\b", re.I)
        self.re_export = export_re()
        self.re_val = re.compile(rf"^(?:{kw('val')})\s+", re.I)
        self.re_annotation = re.compile(r"^[ \t]*&(\S.*?)\s*$")
        pp = lambda k: lx.alt(k, section="pp")  # noqa: E731
        self.re_pp_if = re.compile(rf"^[ \t]*#[ \t]*{pp('if')}\b(?P<cond>.*?)(?:{pp('then')})?\s*$", re.I)
        self.re_pp_elseif = re.compile(rf"^[ \t]*#[ \t]*{pp('elseIf')}\b(?P<cond>.*?)(?:{pp('then')})?\s*$", re.I)
        self.re_pp_else = re.compile(rf"^[ \t]*#[ \t]*{pp('else')}\b", re.I)
        self.re_pp_end = re.compile(rf"^[ \t]*#[ \t]*{pp('endIf')}\b", re.I)
        self.procedure_words = {w.lower() for w in lx.keywords["procedure"]}

    def parse(self, text):
        sanitized, literals = lex(text)
        lines = sanitized.split("\n")
        original = text.split("\n")
        line_starts = [0]
        for ln in lines[:-1]:
            line_starts.append(line_starts[-1] + len(ln) + 1)

        # ветки препроцессора по строкам
        conditions = [()] * (len(lines) + 1)
        stack = []
        for no, ln in enumerate(lines, 1):
            m = self.re_pp_if.match(ln)
            if m:
                stack.append(m.group("cond").strip())
                conditions[no] = tuple(stack)
                continue
            m = self.re_pp_elseif.match(ln)
            if m and stack:
                prev = stack.pop()
                stack.append(f"НЕ ({prev}) И ({m.group('cond').strip()})")
                conditions[no] = tuple(stack)
                continue
            if self.re_pp_else.match(ln) and stack:
                prev = stack.pop()
                stack.append(f"НЕ ({prev})")
                conditions[no] = tuple(stack)
                continue
            if self.re_pp_end.match(ln) and stack:
                conditions[no] = tuple(stack)
                stack.pop()
                continue
            conditions[no] = tuple(stack)

        methods = []
        open_method = None
        no = 1
        pending_annotations = []
        while no <= len(lines):
            ln = lines[no - 1]
            if open_method is None:
                hm = self.re_header.match(ln)
                if hm:
                    offset = line_starts[no - 1] + hm.end()
                    params_text, close = self._balanced(sanitized, offset)
                    rest_line_end = sanitized.find("\n", close)
                    rest_line_end = len(sanitized) if rest_line_end < 0 else rest_line_end
                    rest = sanitized[close + 1:rest_line_end]
                    first = min(pending_annotations) if pending_annotations else no
                    directive, annotations = self._directive(pending_annotations, original)
                    open_method = Method(
                        name=hm.group("name"),
                        kind="procedure" if hm.group("kind").lower() in self.procedure_words else "function",
                        export=bool(self.re_export.match(rest)),
                        params=self._params(params_text),
                        start=first, header=no, end=no,
                        directive=directive, annotations=annotations,
                        is_async=bool(hm.group("async")))
                    pending_annotations = []
                    no = bisect.bisect_right(line_starts, close)  # строка с закрывающей скобкой
                    if self.re_end_inline.search(rest):
                        open_method.end = no             # метод в одну строку
                        methods.append(open_method)
                        open_method = None
                    no += 1
                    continue
                am = self.re_annotation.match(ln)
                if am:
                    pending_annotations.append(no)
                elif ln.strip():
                    pending_annotations = []
            else:
                if self.re_end.match(ln) or self.re_end_inline.search(ln):
                    open_method.end = no
                    methods.append(open_method)
                    open_method = None
            no += 1
        if open_method is not None:           # незакрытый метод — до конца файла
            open_method.end = len(lines)
            methods.append(open_method)

        in_method = set()
        for m in methods:
            in_method.update(range(m.start, m.end + 1))
        code_lines = {i for i, ln in enumerate(lines, 1)
                      if i not in in_method and ln.strip() and not ln.lstrip().startswith("#")}
        return Module(text, methods, literals, conditions, code_lines, sanitized)

    @staticmethod
    def _balanced(text, offset):
        depth = 1
        i = offset
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        return text[offset:i - 1], i - 1

    def _params(self, text):
        params = []
        for part in self._split_top(text):
            part = " ".join(part.split())
            if not part:
                continue
            by_value = bool(self.re_val.match(part))
            if by_value:
                part = self.re_val.sub("", part)
            name, _, default = part.partition("=")
            params.append(Param(name.strip(), by_value, bool(default.strip()) or "=" in part))
        return params

    @staticmethod
    def _split_top(text):
        parts, depth, cur = [], 0, []
        for ch in text:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        parts.append("".join(cur))
        return parts

    def _directive(self, annotation_lines, lines):
        directive, annotations = None, []
        for no in annotation_lines:
            word = lines[no - 1].strip().rstrip(";")[1:]
            key = re.match(_NAME, word)
            mapped = self.lx.directives.get(key.group(0).lower()) if key else None
            if mapped and word.strip() == key.group(0):
                directive = mapped
            else:
                annotations.append(word)
        return directive, annotations


_parser = None


def parse(text):
    global _parser
    if _parser is None:
        _parser = Parser()
    return _parser.parse(text)


def condition_symbols(conditions):
    """Контексты, упомянутые в условиях препроцессора, и признак отрицания ВнешнееСоединение."""
    lx = lexicon()
    symbols, negated_ec = set(), False
    for cond in conditions:
        for word in re.findall(_NAME, cond):
            ctx = lx.preprocessor_symbols.get(word.lower())
            if ctx:
                symbols.add(ctx)
        norm = re.sub(r"\(\s*", "(", " ".join(cond.split()).lower())
        for neg in (w.lower() for w in lx.preprocessor["not"]):
            for ec in (k for k, v in lx.preprocessor_symbols.items() if v == "ExternalConnection"):
                if f"{neg} {ec}" in norm or f"{neg} ({ec}" in norm:
                    negated_ec = True
    return symbols, negated_ec
