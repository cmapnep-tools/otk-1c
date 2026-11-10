# Tasks

## 1. Режим обычного клиента

- [x] 1.1 `extended_check`: режимы `/CheckModules` и контексты покрытия по потребности плана (`OrdinaryClient`),
      `RunContext.ext_contexts`
- [x] 1.2 `runner`: снятие «не покрыто» по проверенным контекстам прогона, а не по статическому списку
- [x] 1.3 `ib_tools`: основной режим запуска временного расширения — как у расширяемой конфигурации
      (`DefaultRunMode` в `base_properties`; найдено на приёмке 8.3.23 — иначе расширение не загружается)

## 2. Обязательное покрытие

- [x] 2.1 `config`: `environment.requiredContexts` (умолчание — пусто)
- [x] 2.2 `collect` и схема плана: `requiredContexts`
- [x] 2.3 `report`: непокрытый обязательный контекст — код 2 и вердикт

## 3. Проверка

- [x] 3.1 Тесты: режим обычного клиента и контексты покрытия, `requiredContexts` в вердикте, сценарий runner,
      режим запуска расширения
- [x] 3.2 `ruff`, `mypy src`, полный `unittest`, `openspec validate --all --strict`, `mkdocs build --strict`
- [x] 3.3 Прогон на платформе 8.3.23 с выгрузкой обычного приложения: контекст `OrdinaryClient` снимается,
      `requiredContexts` даёт код 2, пока покрытия нет
- [x] 3.4 Покрытие только по выполненным проверкам: `--static-only` и `--fast` при `requiredContexts` —
      код 2 без ошибок и код 1 с ними (проверено на стенде 8.3.23)

## 4. Документация

- [x] 4.1 `docs/configuration.md`, `otk.example.toml`: `requiredContexts`
- [x] 4.2 `docs/checks.md`, `docs/limits.md`, `docs/report.md`: режим обычного клиента и обязательное покрытие
