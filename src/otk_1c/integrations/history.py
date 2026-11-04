"""Журнал срабатываний хуков: строка JSON на запуск `otk pt` и pre-push в `<runDir>/history.jsonl`.

Каталог прогонов общий для репозиториев (у каждого — свой подкаталог), журнал — один на все: по нему видно, сколько
раз хуки срабатывали, с каким итогом и за сколько. Сбой записи журнала на проверку не влияет.
"""
import json
import time
from datetime import datetime

from .. import __version__, config
from ..sources import workspace

NAME = "history.jsonl"
MAX_BYTES = 5 * 1024 * 1024     # больше — старый журнал переименовывается в history.1.jsonl


def path(cfg, repo):
    return workspace.run_dir(cfg, repo).parent / NAME


def record(cfg, repo, source, action, code, started, **extra):
    """Дописать срабатывание: source — pt | hook, action — действие, code — код выхода, started — time.time() начала;
    cfg = None — загрузить настройки репозитория здесь (сбой загрузки журнал тоже не роняет)."""
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "source": source, "action": action,
             "repo": str(repo), "code": code, "seconds": round(time.time() - started, 1), "version": __version__}
    entry.update({k: v for k, v in extra.items() if v is not None})
    try:
        if cfg is None:
            cfg = config.load(repo)
        p = path(cfg, repo)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > MAX_BYTES:
            p.replace(p.with_name("history.1.jsonl"))
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:                    # noqa: BLE001, S110 — журнал не должен ронять хук
        pass
    return entry
