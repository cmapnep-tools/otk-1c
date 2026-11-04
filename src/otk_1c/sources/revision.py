"""Чтение файлов выгрузки в редакциях плана «было» и «стало»: git или каталог."""
from pathlib import Path

from . import dirdiff, gitdiff


def read_base(repo, plan):
    """Чтение файла выгрузки в редакции «было» плана: rel -> текст или None (git или каталог)."""
    if plan.get("headDir"):
        base_dir = plan.get("baseDir")
        return (lambda rel: dirdiff.read(base_dir, rel)) if base_dir else (lambda rel: None)
    prefix = gitdiff.source_prefix(plan["source"])
    return lambda rel: gitdiff.show(Path(repo), plan["baseSha"], prefix + rel)


def read_head(repo, plan):
    """Чтение файла выгрузки в проверяемой редакции плана (коммит, рабочее дерево или каталог)."""
    if plan.get("headDir"):
        return lambda rel: dirdiff.read(plan["headDir"], rel)
    prefix = gitdiff.source_prefix(plan["source"])
    ref = gitdiff.WORKTREE if plan["head"] == "WORKTREE" else plan["headSha"]
    return lambda rel: gitdiff.show(Path(repo), ref, prefix + rel)
