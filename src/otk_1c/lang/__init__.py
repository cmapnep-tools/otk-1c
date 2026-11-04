"""Словари вариантов встроенного языка.

Ключевые слова и встроенные имена (`Справочники`/`Catalogs`) платформа принимает в любом варианте языка,
поэтому разбор и статика объединяют все словари (`Lexicon`, `metadata_names`): в коде английской конфигурации
встречаются оба языка. Вариант конфигурации (`ScriptVariant`) должен быть поддержан — полный словарь
(`"complete": true`). Имена владельцев модулей в плане и код проб — всегда русские (`CANONICAL`): расширение
ОТК русское, а платформа исполняет его в конфигурации любого варианта языка.

Раздел `platform` — тексты интерфейса платформы (имена модулей в сообщениях, признаки ошибки компиляции): от
варианта языка конфигурации не зависят, разбор объединяет все словари, где раздел есть (`platform_texts`). Есть
только в русском словаре: при англоязычном интерфейсе платформы ошибки проб не распознаются (docs/limits.md).
"""
import json
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

_DIR = Path(__file__).parent
_FILES = {"Russian": "ru.json", "English": "en.json"}
CANONICAL = "Russian"


class UnsupportedScriptVariant(Exception):
    pass


@lru_cache(maxsize=None)
def load(variant):
    name = _FILES.get(variant)
    if name is None:
        raise UnsupportedScriptVariant(f"вариант языка {variant} не поддерживается")
    return json.loads((_DIR / name).read_text(encoding="utf-8"))


def dictionary(variant):
    """Полный словарь варианта языка конфигурации — для генерации кода и имён метаданных."""
    data = load(variant)
    if not data.get("complete"):
        raise UnsupportedScriptVariant(f"вариант языка {variant} не поддерживается (словарь неполный)")
    return data


def metadata_names():
    """Имена видов метаданных всех вариантов: {"managers": {имя: коллекция}, "singles": {имя: коллекция},
    "forms": {слово «Форма»/«Form»}} — ключи в нижнем регистре."""
    out = {"managers": {}, "singles": {}, "forms": set()}
    for variant in _FILES:
        data = load(variant)
        for collection, info in data.get("metadataTypes", {}).items():
            if info.get("manager"):
                out["managers"][info["manager"].lower()] = collection
            if info.get("single"):
                out["singles"][info["single"].lower()] = collection
        if data.get("words", {}).get("form"):
            out["forms"].add(data["words"]["form"].lower())
    return out


@lru_cache(maxsize=None)
def platform_texts():
    """Тексты интерфейса платформы всех словарей (только чтение — результат общий): {"modules": {имя модуля
    в сообщении: вид модуля}, "compileInText": (...), "compileInMessage": (...)}."""
    modules, in_text, in_message = {}, [], []
    for variant in _FILES:
        data = load(variant).get("platform", {})
        modules.update(data.get("modules", {}))
        in_text.extend(data.get("compileInText", []))
        in_message.extend(data.get("compileInMessage", []))
    return MappingProxyType({"modules": MappingProxyType(modules), "compileInText": tuple(in_text),
                             "compileInMessage": tuple(in_message)})


class Lexicon:
    """Ключевые слова всех вариантов языка для разбора текста (без учёта регистра)."""

    def __init__(self):
        self.keywords = {}
        self.preprocessor = {}
        self.preprocessor_symbols = {}
        self.directives = {}
        self.query_start = []
        self.query_words = []
        for variant in _FILES:
            data = load(variant)
            for section, target in (("keywords", self.keywords), ("preprocessor", self.preprocessor)):
                for key, words in data[section].items():
                    target.setdefault(key, []).extend(words)
            self.preprocessor_symbols.update({k.lower(): v for k, v in data["preprocessorSymbols"].items()})
            self.directives.update({k.lower(): v for k, v in data["directives"].items()})
            self.query_start.extend(data["queryStart"])
            self.query_words.extend(data["queryWords"])

    def alt(self, *keys, section="keywords"):
        """Регулярное выражение-альтернатива для слов из словаря."""
        table = self.keywords if section == "keywords" else self.preprocessor
        words = [w for k in keys for w in table[k]]
        return "(?:" + "|".join(sorted(words, key=len, reverse=True)) + ")"


@lru_cache(maxsize=None)
def lexicon():
    return Lexicon()
