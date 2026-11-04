"""Проверки перед push — те же, что шаги ruff, mypy и openspec в CI.

Включение (один раз на клон): git config core.hooksPath .githooks
Инструмент ищется так: переменная RUFF / MYPY / OPENSPEC → git config otk.ruff / otk.mypy / otk.openspec →
PATH; не найден — предупреждение, проверка пропускается (её выполнит CI). Запуск вручную: python .githooks/lint.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = (("ruff", "RUFF", ["check", "--no-cache", "--output-format", "concise", "src", "tests"]),
         ("mypy", "MYPY", []),
         ("openspec", "OPENSPEC", ["validate", "--all", "--strict", "--no-interactive"]))


def find(name, env):
    configured = subprocess.run(["git", "-C", str(ROOT), "config", f"otk.{name}"], capture_output=True,
                                text=True).stdout.strip()
    path = os.environ.get(env) or configured or shutil.which(name)
    return path if path and os.path.exists(path) else None


def main():
    failed = []
    for name, env, args in TOOLS:
        cli = find(name, env)
        if not cli:
            print(f"pre-push: {name} не найден ({env}, git config otk.{name} или PATH) — проверка пропущена, её "
                  "выполнит CI", file=sys.stderr)
            continue
        r = subprocess.run([cli, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode:
            failed.append(name)
            out = "\n".join(ln for ln in (r.stdout + r.stderr).splitlines() if "[INFO]" not in ln and "✓" not in ln)
            print(f"pre-push: {name} — ошибки:\n{out.strip()}", file=sys.stderr)
    guard = ROOT / "tools" / "publish.py"          # сторож дерева публикации — только в лабораторном репозитории
    if guard.is_file():
        r = subprocess.run([sys.executable, str(guard), "check"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            failed.append("publish")
            print(f"pre-push: сторож публикации — находки:\n{r.stdout.strip()}", file=sys.stderr)
    if failed:
        print(f"pre-push: push отменён ({', '.join(failed)}); ruff: `ruff check --fix src tests`", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
