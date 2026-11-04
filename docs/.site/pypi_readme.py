"""README для PyPI: относительные ссылки и картинки — на сайт документации и файлы репозитория.

    python docs/.site/pypi_readme.py      переписать README.md на месте — только в сборке пакета (CI, выпуск)

На GitHub и на сайте README читается с относительными ссылками (сайт переписывает их сам, hooks.py); PyPI их не
разрешает, поэтому перед `python -m build` ссылки становятся абсолютными: страницы docs/ — сайт, картинки docs/img/
— сайт (GitHub Pages отдаёт SVG картинкой), остальное — файл в репозитории.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SITE = "https://cmapnep-tools.github.io/otk-1c/"
REPO = "https://github.com/cmapnep-tools/otk-1c/"
LINK = re.compile(r"(\]\(|<img src=\"|<a href=\")([^)\"#\s]*)(#[^)\"\s]*)?")


def url(path: str, anchor: str = "") -> str:
    if path.startswith("docs/img/"):
        return SITE + path[len("docs/"):]
    if path.startswith("docs/") and path.endswith(".md"):
        return SITE + path[len("docs/"):-len(".md")] + "/" + anchor        # use_directory_urls
    kind = "tree" if path.endswith("/") or (ROOT / path).is_dir() else "blob"
    return f"{REPO}{kind}/master/{path}{anchor}"


def rewrite(text: str) -> str:
    def sub(m):
        head, target, anchor = m.group(1), m.group(2), m.group(3) or ""
        if not target or re.match(r"[a-z]+:", target):
            return m.group(0)
        return head + url(target, anchor)
    return LINK.sub(sub, text)


if __name__ == "__main__":
    readme = ROOT / "README.md"
    readme.write_text(rewrite(readme.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"{readme.name}: ссылки абсолютные")
