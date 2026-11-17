## Implementation
- [x] Добавить контроль undeclared-output-arg только с платформенными ожиданиями.
- [x] Проверить генерацию дефекта для Designer XML и EDT.
- [x] Описать границу static-only и два корректных варианта.
- [x] Выполнить точечные тесты, Ruff и OpenSpec validate --strict.

## Context sources
- План проверки основан на воспроизведении в новой пустой ИБ 8.3.23.2236; реальные базы не использовались.

## Проверки DoD
- Каталог формирует отдельный дефект для Designer XML и EDT: `test_undeclared_output_argument` passed.
- Регрессия каталога: `python -m unittest discover -s tests -p test_edt_controls.py -q` — 13 passed.
- Раскладка и сопоставление контрольных дефектов: `test_controls_batches.py` — 19 passed.
- `ruff check src/otk_1c/extras/controls.py tests/test_edt_controls.py` — passed; `git diff --check` — passed.
- `openspec validate cover-undefined-output-argument --strict` — valid.
- Платформенный пример и два положительных контроля: результаты в `design.md` и локальном `results.json`.
