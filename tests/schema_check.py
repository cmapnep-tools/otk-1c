"""Минимальный валидатор JSON Schema для тестов контрактов файлов шагов (без внешних зависимостей).

Подмножество: type (строка или список), required, properties, additionalProperties: false, items, enum, const,
$ref на #/$defs/<имя>.

Публикуемые схемы открыты (читатель игнорирует незнакомые поля, открытое перечисление — anyOf [enum, string]);
тесты ОТК проверяют строгую копию (strict): объекты из CLOSED закрыты, открытое перечисление — только известные.
"""
import json
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent.parent / "src" / "otk_1c" / "schema"
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
# публикуемая схема -> объекты, описанные полностью ("" — корень, иначе имя в $defs): ОТК не пишет лишних полей
CLOSED = {"summary": ["", "finding"], "gate-result": [""], "doctor-result": [""], "report-result": [""],
          "build-result": [""], "console-result": [""], "platform-help-result": [""]}


def _strict(node):
    if isinstance(node, list):
        return [_strict(x) for x in node]
    if not isinstance(node, dict):
        return node
    node = {k: _strict(v) for k, v in node.items()}
    variants = node.get("anyOf")
    if variants and len(variants) == 2 and "enum" in variants[0] and variants[1] == {"type": "string"}:
        node = {**{k: v for k, v in node.items() if k != "anyOf"}, "enum": variants[0]["enum"]}
    return node


def load(name, strict=True):
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    if not strict:
        return schema
    schema = _strict(schema)
    for part in CLOSED.get(name, []):
        (schema["$defs"][part] if part else schema)["additionalProperties"] = False
    return schema


def _is(value, type_):
    if type_ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[type_])


def errors(value, schema, root=None, path="$"):
    """Список нарушений: пусто — значение соответствует схеме."""
    root = root or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            return errors(value, root["$defs"][name], root, path)
        name = Path(ref.split("#", 1)[0]).name.removesuffix(".v1.json")
        return errors(value, load(name), path=path)
    out = []
    if "anyOf" in schema and not any(not errors(value, variant, root, path) for variant in schema["anyOf"]):
        return [f"{path}: не соответствует ни одной схеме"]
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, t) for t in types):
            return [f"{path}: ожидается {'|'.join(types)}, получено {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: ожидается {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} не из {schema['enum']}")
    if isinstance(value, dict):
        out += [f"{path}: нет {key}" for key in schema.get("required", []) if key not in value]
        if schema.get("additionalProperties") is False:
            out += [f"{path}: лишнее поле {key}" for key in value if key not in schema.get("properties", {})]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                out += errors(value[key], sub, root, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += errors(item, schema["items"], root, f"{path}[{i}]")
    return out
