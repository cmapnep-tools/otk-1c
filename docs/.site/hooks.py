"""Хук сборки сайта: главная страница — README.md, ссылки за пределы docs/ — на файлы в репозитории GitHub.

Документы пишутся для чтения на GitHub (README в корне, страницы в docs/, ссылки относительные); на сайте те же
тексты: в README `docs/x.md` становится `x.md`, а `LICENSE`, `CHANGELOG.md`, `../openspec/...` и подобное —
ссылкой на файл в репозитории.

Схемы файлов ОТК (src/otk_1c/schema/) публикуются по адресу из своего `$id`: schema/<имя>.v1.json.
"""
import json
import os
import re

from mkdocs.structure.files import File

LINK = re.compile(r"(\]\(|<img src=\"|<a href=\")([^)\"#\s]*)(#[^)\"\s]*)?")


def _blob(config, path):
    kind = "tree" if path.endswith("/") else "blob"
    return f"{config['repo_url'].rstrip('/')}/{kind}/master/{path}"


def _on_site(files, path):
    """Страница или файл docs/ попадает на сайт (каталоги и исключённые exclude_docs — нет)."""
    f = files.get_file_from_path(path[len("docs/"):]) if files is not None else None
    return f is not None and not f.inclusion.is_excluded()


def _rewrite(text, config, from_root, files=None):
    def sub(m):
        head, target, anchor = m.group(1), m.group(2), m.group(3) or ""
        if not target or re.match(r"[a-z]+:", target):
            return m.group(0)
        path = target if from_root else os.path.normpath(os.path.join("docs", target)).replace(os.sep, "/")
        if path == "README.md":  # страницы docs/ лежат рядом с главной
            return f"{head}index.md{anchor}"
        if path.startswith("docs/") and (from_root or _on_site(files, path)):
            return f"{head}{path[len('docs/'):] if from_root else target}{anchor}"
        if os.path.isdir(os.path.join(os.path.dirname(config["config_file_path"]), path)):
            path += "/"
        return f"{head}{_blob(config, path)}{anchor}"
    return LINK.sub(sub, text)


def on_files(files, config):
    root = os.path.dirname(config["config_file_path"])
    with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
        text = f.read()
    files.append(File.generated(config, "index.md", content=_rewrite(text, config, from_root=True)))
    schemas = os.path.join(root, "src", "otk_1c", "schema")
    for name in sorted(os.listdir(schemas)):
        with open(os.path.join(schemas, name), encoding="utf-8") as f:
            text = f.read()
        uri = json.loads(text)["$id"]
        if uri.startswith(config["site_url"]):
            files.append(File.generated(config, uri[len(config["site_url"]):], content=text))
    return files


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri == "index.md":
        return markdown
    return _rewrite(markdown, config, from_root=False, files=files)
