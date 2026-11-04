"""Устройство пакета (спецификация architecture): слои без обратных зависимостей, без циклов импорта."""
import ast
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "otk_1c"

# слой модуля — по подпакету (корневые модули — по имени); импорт разрешён только в свой слой и ниже
LAYERS = {
    "otk_1c": 0, "otk_1c.lang": 0, "otk_1c.errors": 0, "otk_1c.paths": 0,
    "otk_1c.sources": 1,
    "otk_1c.config": 2, "otk_1c.model": 2, "otk_1c.checks": 2,
    "otk_1c.probes": 3,
    "otk_1c.analyzers": 4,
    "otk_1c.gate": 5,
    "otk_1c.integrations": 6,
    "otk_1c.extras": 7,
    "otk_1c.cli": 8, "otk_1c.__main__": 9,
}


def modules():
    out = {}
    for p in PKG.rglob("*.py"):
        parts = p.relative_to(PKG.parent).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        out[name] = p
    return out


def layer(name):
    while name not in LAYERS:
        name = name.rpartition(".")[0]
    return LAYERS[name]


def imports(name, path, mods):
    """(импортируемый модуль пакета, импортированное имя, строка) — все импорты, в том числе внутри функций."""
    is_pkg = path.name == "__init__.py"
    pkg = name.split(".") if is_pkg else name.split(".")[:-1]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(pkg[: len(pkg) - node.level + 1] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if not base.startswith("otk_1c"):
                continue
            for a in node.names:
                full = f"{base}.{a.name}"
                yield (full if full in mods else base), a.name, node.lineno
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("otk_1c"):
                    yield a.name, None, node.lineno


class LayersTest(unittest.TestCase):
    def setUp(self):
        self.mods = modules()

    def test_no_upward_imports(self):
        bad = []
        for name, path in self.mods.items():
            for target, _, line in imports(name, path, self.mods):
                if layer(target) > layer(name):
                    bad.append(f"{path.relative_to(PKG)}:{line} {name} -> {target}")
        self.assertEqual(bad, [], "импорт из слоя выше")

    def test_no_private_names_of_other_modules(self):
        bad = []
        for name, path in self.mods.items():
            for target, imported, line in imports(name, path, self.mods):
                if imported and imported.startswith("_") and not imported.startswith("__") and target != name:
                    bad.append(f"{path.relative_to(PKG)}:{line} {imported} из {target}")
        self.assertEqual(bad, [])

    def test_no_import_cycles(self):
        graph = {name: set() for name in self.mods}
        for name, path in self.mods.items():
            for target, _, _ in imports(name, path, self.mods):
                # пакет импортирует свои модули (реестр, переэкспорт) — это не цикл
                if target != name and not target.startswith(name + "."):
                    graph[name].add(target)
        state, cycles = {}, []

        def visit(v, stack):
            state[v] = 1
            stack.append(v)
            for w in sorted(graph.get(v, ())):
                if state.get(w) == 1:
                    cycles.append(" -> ".join(stack[stack.index(w):] + [w]))
                elif w not in state:
                    visit(w, stack)
            stack.pop()
            state[v] = 2

        for v in sorted(graph):
            if v not in state:
                visit(v, [])
        self.assertEqual(cycles, [])

    def test_cli_imported_only_by_entry_point(self):
        users = [name for name, path in self.mods.items()
                 if any(t == "otk_1c.cli" for t, _, _ in imports(name, path, self.mods))]
        self.assertEqual(users, ["otk_1c.__main__"])


if __name__ == "__main__":
    unittest.main()
