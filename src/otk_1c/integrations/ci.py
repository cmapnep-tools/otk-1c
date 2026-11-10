"""Запуск в CI: Forgejo/Gitea Actions, GitHub Actions, GitLab CI, Jenkins (шаблоны — docs/ci.md).

    otk ci gate [--repo <checkout>] [--base <ref>] [--designer] [--artifacts <каталог>]   gate + артефакты, код gate
    otk ci designer-check [--repo <checkout>] [--artifacts <каталог>]   все модули Конфигуратором + артефакты

Без --repo — checkout_dir(): каталог проекта по GITHUB_WORKSPACE и GITHUB_REPOSITORY, иначе текущий.
Без --base — целевая ветка запроса на слияние из переменных CI (pr_target): origin/<ветка>, которой нет в checkout,
догружается (ensure_base); не запрос на слияние — --base обязателен (проверка в cli).

Машинные пути задание передаёт переменными окружения OTK_IB, OTK_USER, OTK_PASSWORD,
OTK_PLATFORM, OTK_YAXUNIT, OTK_BSLLS, OTK_RUN_DIR (config.ENV_VARS) — они, как везде, важнее otk.toml и
otk.local.toml; в файлы checkout не пишутся (пароль на диске не остаётся). Копия ИБ и каталог прогона живут между
запусками: от состояния сборки зависит, будет ли загрузка частичной.

Артефакты: с --artifacts — копии в каталог, его забирает сама система CI (actions/upload-artifact@v4, artifacts
GitLab, archiveArtifacts Jenkins). Без него — загрузка самой ОТК (forgejo_artifacts): на раннере Forgejo без
node действия JavaScript недоступны.
"""
import os
import shutil
from pathlib import Path

from .. import config
from ..gate import pipeline, report
from ..probes import designer_check
from ..sources import gitdiff, workspace
from . import forgejo_artifacts

# Целевая ветка запроса на слияние: GitHub Actions и Forgejo/Gitea, GitLab CI, Jenkins (multibranch)
TARGET_VARS = ("GITHUB_BASE_REF", "CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "CHANGE_TARGET")
GATE_ARTIFACTS = ("summary.html", "summary.md", "summary.json", "junit.xml", "problems.txt", "summary.sarif")
DESIGNER_ARTIFACTS = ("designer-check.md", "junit-designer-check.xml")
PUBLIC_ARTIFACTS = set(GATE_ARTIFACTS) | set(DESIGNER_ARTIFACTS)
PRIVATE_ARTIFACTS = {"otk.json", "static.json", "run.json", "designer.json", "designer-check.json",
                     "designer-check.out", "control-check.out", "control-check-ext.out"}


def collect_artifacts(files, dest, run_dir):
    """Копии файлов в каталог dest (logs/x.out -> x.out) для загрузки средствами CI. Копия файла, которого
    в этом прогоне нет, удаляется: рабочий каталог Jenkins живёт между запусками. Возвращает число файлов."""
    files = list(map(Path, files))
    dest = Path(dest)
    run_dir = Path(run_dir).resolve()
    target = dest.resolve()
    if target == run_dir or target in run_dir.parents or run_dir in target.parents:
        raise ValueError(f"каталог --artifacts {dest} пересекается с каталогом прогона {run_dir}")
    names = {path.name for path in files}
    managed = PRIVATE_ARTIFACTS | PUBLIC_ARTIFACTS
    for name in managed:
        path = dest / name
        if path.is_symlink():
            raise ValueError(f"путь артефакта {path} является символической ссылкой")
        resolved = path.resolve()
        if run_dir == resolved or run_dir in resolved.parents:
            raise ValueError(f"путь артефакта {path} пересекается с каталогом прогона {run_dir}")
    dest.mkdir(parents=True, exist_ok=True)
    for name in PRIVATE_ARTIFACTS:
        (dest / name).unlink(missing_ok=True)
    for name in PUBLIC_ARTIFACTS - names:
        (dest / name).unlink(missing_ok=True)
    n = 0
    for f in files:
        target = dest / f.name
        if f.is_file():
            shutil.copyfile(f, target)
            n += 1
        elif target.is_file():
            target.unlink()
    return n


def _publish(name, run_dir, names, log, dest=None):
    files = [Path(run_dir) / n for n in names]
    try:
        if dest:
            log(f"артефакты: {collect_artifacts(files, dest, run_dir)} файлов в {dest}")
        else:
            forgejo_artifacts.upload(name, files, log=log)
    except Exception as e:  # noqa: BLE001 - артефакты не должны менять вердикт проверки
        log(f"артефакты: {'копирование' if dest else 'загрузка'} не удалась: {e}")


def pr_target(environ=None):
    """Целевая ветка запроса на слияние из переменных CI или None (не запрос: push, расписание, ручной запуск)."""
    env = os.environ if environ is None else environ
    return next((env[v] for v in TARGET_VARS if env.get(v)), None)


def ensure_base(repo, branch, log=print):
    """origin/<ветка> для --base. GitLab и Jenkins берут в checkout только ветку запроса, actions/checkout — один
    коммит: нет ветки — догрузить, неполная история — догрузить целиком (общий предок нужен для отличий)."""
    ref = f"origin/{branch}"
    if gitdiff.git(repo, "rev-parse", "--is-shallow-repository", check=False).strip() == "true":
        log("история неполная: догружаю целиком")
        gitdiff.git(repo, "fetch", "-q", "--unshallow", "origin")
    if not gitdiff.git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False).strip():
        log(f"базовая ветка: догружаю {ref}")
        gitdiff.git(repo, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/{ref}")
    return ref


def checkout_dir(environ=None):
    """Каталог проекта в задании CI без --repo: GITHUB_WORKSPACE/<имя из GITHUB_REPOSITORY> (шаблоны клонируют
    туда). Эти переменные есть во всех событиях, в отличие от github.event.repository (в schedule пусто)."""
    env = os.environ if environ is None else environ
    ws, full = env.get("GITHUB_WORKSPACE"), env.get("GITHUB_REPOSITORY", "")
    if ws and full:
        d = Path(ws) / full.split("/")[-1]
        if d.is_dir():
            return str(d)
    return "."


def main(a):

    log = lambda m: print(m, flush=True)   # noqa: E731
    repo = Path(gitdiff.git(a.repo or checkout_dir(), "rev-parse", "--show-toplevel").strip())
    cfg = config.load(repo)
    log(f"из переменных OTK_*: {', '.join(sorted(config.env_values())) or 'ничего'}")
    run_dir = workspace.run_dir(cfg, repo)
    dest = getattr(a, "artifacts", None)
    if a.action == "gate":
        base = a.base
        if not base and (target := pr_target()):
            base = ensure_base(repo, target, log)
        log(f"база: {base}")
        summary = pipeline.gate_run(repo, cfg, run_dir, pipeline.GateOptions(base, designer=a.designer), log=log)
        log(report.markdown(summary))
        _publish("otk-gate", run_dir, GATE_ARTIFACTS, log, dest)
        return summary["code"]
    summary = designer_check.run(repo, cfg, run_dir, control=not a.no_control, log=log)
    log(designer_check.markdown(summary))
    _publish("otk-designer-check", run_dir, DESIGNER_ARTIFACTS, log, dest)
    return summary["code"]
