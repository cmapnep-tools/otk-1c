## MODIFIED Requirements

### Requirement: Итог команды report в JSON

С ключом `report --json` ОТК SHALL печатать в stdout один объект `otk.report-result.v1` по схеме `schema/report-result.schema.json`, содержащий итоговый `summary` и путь к нему; журнал SHALL идти в stderr, а код выхода SHALL совпадать с обычным `report`. Если план отсутствует, имеет неподдерживаемую схему или сводку нельзя собрать, команда SHALL вернуть код 2 и объект со `status` `infra`, `reasonCode` и `remediation`.

#### Scenario: Сводка прогона

- **WHEN** каталог прогона содержит план текущей схемы и файлы шагов
- **THEN** `report --json` возвращает `otk.report-result.v1` с `summary.schema` `otk.summary.v1`, а stdout содержит только JSON

#### Scenario: Нет плана

- **WHEN** `report --json` запущен для каталога без `otk.json`
- **THEN** код выхода 2, stdout содержит один объект со `status` `infra` и `reasonCode` `no-plan`, журнал — в stderr

#### Scenario: Неподдерживаемая схема плана

- **WHEN** `otk.json` имеет `schema`, отличную от текущей `otk.plan.v1`, либо не содержит `schema`
- **THEN** чтение завершается до файлов шагов, материализации редакции и записи сводки; `report --json` возвращает код 2, один объект со `status` `infra`, `reasonCode` `plan-version` и подсказкой повторить `collect` или `gate`; обычный `report` возвращает код 2 и ту же причину в stderr

## Context sources

Подтверждено кодом `gate.report.from_files`, `cli._report_data` и `model.PLAN_SCHEMA`. Исполнение 1С не проверялось.
