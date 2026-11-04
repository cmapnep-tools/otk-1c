# Разработка

## Документы (OpenSpec)

Требования — в формате [OpenSpec](https://github.com/Fission-AI/OpenSpec):

- [openspec/project.md](../openspec/project.md) — назначение, принцип, технологии, соглашения, ограничения платформы
- [openspec/specs/](../openspec/specs/) — действующие требования со сценариями по областям: сбор изменений,
  статика, пробы компиляции, запросы, сборка копии ИБ, раннер, сценарии, формы, сводка, CI, контрольные дефекты,
  формат EDT, архитектура, консоль ИБ, анализ ТЖ

Новое поведение — сначала изменение OpenSpec (`openspec/changes/<изменение>/`: proposal, design, tasks, дельты
требований), `openspec validate <изменение> --strict`, затем код.

## Код

- [adding-a-check.md](adding-a-check.md) — как добавить проверку: исполнитель, правило статики, реестры
- [findings.md](findings.md) — справочник кодов находок (тест `FindingsDocTest` следит, что описан каждый код)
- [img/README.md](img/README.md) — как пересобрать картинки README

Тесты: `python -m unittest discover -s tests` из корня (с `PYTHONPATH=src`). Тесты на внешних программах и копии
ИБ идут только при заданных переменных `OTK_TEST_*` (путь к BSL LS, клон с историей, справка платформы), без них
пропускаются.

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — на каждый push в `master` и запрос на слияние:

- `lint` — `ruff check src tests`, `mypy` (типы на границах пакета) и `openspec validate --all --strict`;
- `tests` — юнит-тесты на Windows и Linux, Python 3.11 и 3.14; платформа 1С не нужна;
- `package` — сборка sdist и колеса, установка колеса в чистое окружение, `otk --version` и данные пакета.
  Собранное — артефакт `dist` запуска.

Пробы с платформой 1С (копия ИБ, Конфигуратор, YAxUnit) на облачных раннерах не запускаются: их проверяют
`otk controls` на своём стенде ([commands.md](commands.md), [comparison.md](comparison.md)).

Метки репозитория задаёт `.github/labels.yml` (задание `labels` синхронизирует их при изменении файла),
обновления действий CI предлагает Dependabot (`.github/dependabot.yml`), ревью PR — CodeRabbit (`.coderabbit.yaml`).

## Выпуск

Версия поднимается только при выпуске (SemVer), изменения до него копятся в разделе «Не выпущено»
[CHANGELOG.md](../CHANGELOG.md). Выпуск:

1. PR: раздел «Не выпущено» → `## [X.Y.Z] - ГГГГ-ММ-ДД` (и пустой «Не выпущено» над ним), версия в `pyproject.toml`.
2. После слияния — тег на коммит `master`: `git tag -a vX.Y.Z -m "ОТК X.Y.Z"`, `git push origin vX.Y.Z`.
3. Задание `release` (`.github/workflows/release.yml`) сверяет тег с версией и CHANGELOG, собирает пакет, публикует
   его на PyPI (окружение `pypi` с подтверждением сопровождающего) и создаёт GitHub Release с колесом и заметками
   по меткам PR (`.github/release.yml`).

Исправления выходят для последней минорной версии; ветка `release/X.Y.x` — только когда нужно исправить старую.
