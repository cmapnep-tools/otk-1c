# Tasks

## 1. Формат файлов

- [x] 1.1 `ib_tools`: `FORMAT_MAX`, таблица форматов платформы, `format_for_platform`, `file_format`,
      `generated_format`, `require_format`; формат в `base_properties`
- [x] 1.2 `adopted_object`, `generate_test_extension`, `write_common_module` — версия формата параметром
- [x] 1.3 `extended_check._form_files` — `Form.xml` и описание в формате расширения
- [x] 1.4 `runner._run` — ранняя `require_format` до сборки копии ИБ; `scaffold`, `availability`, `install` —
      платформа при генерации

## 2. Реестр проверок

- [x] 2.1 `extended_check.Failed`; полный сбой `run`/`run_extension`/`designer_root` — исключение
- [x] 2.2 `ExtCheck.run` — `ctx.gate.failed("extcheck", …)`, без per-module `skipped`
- [x] 2.3 `reasons`: `format-unsupported` в `CODES` и `_PATTERNS`; enum схемы; `docs/agents.md`; CHANGELOG

## 3. Проверки

- [x] 3.1 Тесты форматов 2.16/2.17/2.20, EDT и потолка, `require_format`
- [x] 3.2 `test_extcheck_forms`: фикстура 2.17, полный сбой, загрузка расширения
- [x] 3.3 `test_edt_ib`, `test_extension`, `test_reasons` — новое поведение
- [x] 3.4 `ruff`, `mypy`, полный `unittest` (652 теста), `openspec validate --all --strict`
- [x] 3.5 Контрольный прогон на установленных 8.3.23.2236 и 8.3.24.1819 (стенд: ИБ платформы, выгрузка её
      формата — 2.16/2.17): extcheck находит внесённую ошибку (`extcheck/compile-error`, код 1), файлы
      временного расширения — 2.16/2.17; расширение заготовок грузится платформой; выгрузка 2.20 на 8.3.23 —
      код 2 с `format-unsupported` до сборки копии ИБ
- [x] 3.6 `otk controls` на демо-проекте (8.5.1): все контрольные дефекты пойманы ожидаемыми шагами

## 4. controls

- [x] 4.1 `step_key`: шаг статики — по `rule` сводки (поля `code` с 1.0.0 нет) + тест
- [x] 4.2 Ревью: `file_format` — нечитаемый файл и неразбираемая версия — сбой инфраструктуры с путём;
      выгрузка без версии формата получает формат платформы, а не потолок
