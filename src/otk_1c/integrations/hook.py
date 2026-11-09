"""Git-хук pre-push: `gate` для каждой отправляемой ветки.

    otk hook install [--repo .] [--force]   записать хук в каталог хуков репозитория
    otk hook uninstall [--repo .]           удалить хук, если его ставил ОТК
    otk hook run <remote> <url>             тело хука: читает строки pre-push со stdin

Диапазон проверки — от ревизии, которая уже есть на удалённой стороне, до отправляемой; для новой
ветки — от merge-base с базовой веткой ([hook] base, по умолчанию origin/master). Удаление ветки не
проверяется. Код ≠ 0 отменяет push; обход — штатный `git push --no-verify`.

Настройки [hook] в otk.toml: base, mode ("full" — полная проверка в копии ИБ или рабочей базе
environment.connection, "static" — только статика: удобно, когда полная проверка идёт в CI), fast,
infraFail ("block" | "warn" — код 2, сбой инфраструктуры, например база занята, не блокирует push).
"""
import sys
import time
from pathlib import Path

from .. import config
from ..errors import InfraError
from ..gate import pipeline, report, report_html
from ..sources import gitdiff, workspace
from . import history

MARKER = "# otk: pre-push"
ZERO = "0" * 40
HOOK_DEFAULTS = {"base": "origin/master", "mode": "full", "fast": False, "infraFail": "block"}


def hook_path(repo):
    """Путь к pre-push с учётом core.hooksPath и worktree."""
    hooks = gitdiff.git(repo, "rev-parse", "--git-path", "hooks").strip()
    path = Path(hooks)
    return (path if path.is_absolute() else Path(repo) / path) / "pre-push"


def script(python=None, src=None):
    """Текст хука: sh (Git for Windows и Unix), запускает `python -m otk_1c hook run`."""
    python = Path(python or sys.executable).as_posix()
    src = Path(src or Path(__file__).resolve().parents[2]).as_posix()
    sep = ";" if sys.platform == "win32" else ":"
    return (f"#!/bin/sh\n{MARKER} (otk hook install; обход - git push --no-verify)\n"
            f'PYTHONPATH="{src}${{PYTHONPATH:+{sep}$PYTHONPATH}}"\n'
            f"export PYTHONPATH\n"
            f'exec "{python}" -m otk_1c hook run "$@"\n')


def install(repo, force=False):
    path = hook_path(repo)
    if path.exists() and MARKER not in path.read_text(encoding="utf-8", errors="replace") and not force:
        raise FileExistsError(f"{path}: чужой хук pre-push (перезаписать — --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script(), encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def uninstall(repo):
    path = hook_path(repo)
    if not path.exists():
        return None
    if MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise FileExistsError(f"{path}: хук pre-push ставил не ОТК — не трогаю")
    path.unlink()
    return path


def plan_change(repo, action, force=False):
    """План install/uninstall без записи и удаления файлов."""
    path = hook_path(repo)
    exists = path.exists()
    owned = exists and MARKER in path.read_text(encoding="utf-8", errors="replace")
    if action == "install":
        if not exists:
            return {"action": "create", "path": str(path), "blocked": False}
        if owned:
            return {"action": "no-op", "path": str(path), "blocked": False}
        return {"action": "replace" if force else "blocked", "path": str(path), "blocked": not force}
    if not exists:
        return {"action": "no-op", "path": str(path), "blocked": False}
    return {"action": "delete" if owned else "blocked", "path": str(path), "blocked": not owned}


def settings(cfg):
    return {**HOOK_DEFAULTS, **cfg.get("hook", {})}


def ranges(repo, lines, base_ref):
    """Строки pre-push -> [(ветка, base, head)]; head без изменений относительно base пропускается."""
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if local_sha == ZERO:              # удаление ветки
            continue
        base = remote_sha if remote_sha != ZERO and _exists(repo, remote_sha) else None
        if base is None:                   # новая ветка или удалённой ревизии нет локально
            try:
                base = gitdiff.git(repo, "merge-base", local_sha, base_ref).strip()
            except gitdiff.GitError as e:
                raise gitdiff.GitError(f"{local_ref}: нет общей ревизии с {base_ref} ({e})") from e
        if base != local_sha:
            out.append((local_ref, base, local_sha))
    return out


def _exists(repo, sha):
    try:
        gitdiff.git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except gitdiff.GitError:
        return False


def run(repo, lines, log=print, gate=None):
    """Тело хука. Возвращает код выхода (0 — push разрешён)."""

    gate = gate or pipeline.gate_run
    cfg = config.load(repo)
    hs = settings(cfg)
    code = 0
    try:
        todo = ranges(repo, lines, hs["base"])
    except gitdiff.GitError as e:
        log(f"otk pre-push: {e}")
        todo, code = [], _infra(hs, log)
    for ref, base, head in todo:
        log(f"otk pre-push: {ref} {base[:10]}..{head[:10]}")
        run_dir = workspace.run_dir(cfg, repo)
        try:
            opts = pipeline.GateOptions(base, head, static_only=hs["mode"] == "static",
                                        fast=hs["fast"])
            summary = gate(repo, cfg, run_dir, opts, log=log)
        except (InfraError, gitdiff.GitError) as e:
            log(f"otk pre-push: {e}")
            code = max(code, _infra(hs, log))
            continue
        log(report.markdown(summary))
        log(f"сводка: {run_dir / 'summary.md'} (HTML: {run_dir / 'summary.html'})")
        rc = summary["code"]
        kind = cfg.get("report", {}).get("open")
        if rc and kind:                 # push отменяется — сводка нужна; при успехе не мешаем
            report_html.open_summary(run_dir, kind, log)
        if rc == 2:
            rc = _infra(hs, log)
        code = max(code, rc)
    if code:
        log("otk pre-push: push отменён (обход — git push --no-verify)")
    return code


def _infra(hs, log):
    if hs["infraFail"] == "warn":
        log("otk pre-push: сбой инфраструктуры, push разрешён (infraFail = \"warn\")")
        return 0
    return 2


def main(a):
    repo = Path(gitdiff.git(a.repo, "rev-parse", "--show-toplevel").strip())
    if getattr(a, "dry_run", False):
        if a.action == "run":
            raise ValueError("--dry-run доступен только для hook install|uninstall")
        plan = plan_change(repo, a.action, a.force)
        print("План хука (без изменений):")
        print(f"  {plan['action']}: {plan['path']}")
        if plan["blocked"]:
            print("  операция не будет выполнена: хук принадлежит не ОТК или нужен --force")
        return 2 if plan["blocked"] else 0
    if a.action == "install":
        print(f"установлен: {install(repo, a.force)}")
        return 0
    if a.action == "uninstall":
        path = uninstall(repo)
        print(f"удалён: {path}" if path else "хука pre-push нет")
        return 0
    started, code = time.time(), 2
    try:
        code = run(repo, sys.stdin.read().splitlines(), log=lambda m: print(m, flush=True))
        return code
    finally:
        history.record(None, repo, "hook", "pre-push", code, started)
