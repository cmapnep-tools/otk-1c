"""README для PyPI: относительные ссылки становятся абсолютными (docs/.site/pypi_readme.py)."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pypi_readme", ROOT / "docs" / ".site" / "pypi_readme.py")
pypi_readme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pypi_readme)


class PypiReadmeTest(unittest.TestCase):
    def test_links(self):
        text = ('<img src="docs/img/logo.svg"> [агенты](docs/agents.md#коды-причин) [лицензия](LICENSE) '
                '[сайт](https://example.org/x) [раздел](#установка)')
        self.assertEqual(pypi_readme.rewrite(text),
                         '<img src="https://cmapnep-tools.github.io/otk-1c/img/logo.svg"> '
                         '[агенты](https://cmapnep-tools.github.io/otk-1c/agents/#коды-причин) '
                         '[лицензия](https://github.com/cmapnep-tools/otk-1c/blob/master/LICENSE) '
                         '[сайт](https://example.org/x) [раздел](#установка)')

    def test_readme_has_no_relative_links(self):
        out = pypi_readme.rewrite((ROOT / "README.md").read_text(encoding="utf-8"))
        relative = [m.group(2) for m in pypi_readme.LINK.finditer(out)
                    if m.group(2) and not re.match(r"[a-z]+:", m.group(2))]
        self.assertEqual(relative, [])


if __name__ == "__main__":
    unittest.main()
