## Реализация
- [x] Зафиксировать требования и пройти strict validate.
- [x] Удалить недоказуемое восстановление строки и пояснить сравнение кратности.
- [x] Проверить старое обращение и комментарии, точную позицию и неизвестный режим.
- [x] Выполнить тесты, Ruff и git diff --check; записать доказательства DoD.

## Доказательства DoD
- Старое обращение и комментарий не получают позицию: test_increased_count_does_not_locate_old_reference_or_comment — passed, line отсутствует; сообщение явно указывает кратность и неизвестную позицию.
- Точная строка платформы сохраняется: test_platform_position_is_preserved — passed, line=3; преобразование строк перехватчиков дополнительно покрыто существующими test_extcheck_forms.
- Неизвестный режим не объявляется доказанным сравнением кратности: test_unknown_mode_does_not_claim_proven_count — passed, UNCOMPARED сохранён.
- Проверки 17.11.2026: unittest test_extcheck_location (3), test_extcheck_forms (34), test_edt_extcheck (6) — 43 passed. Ruff check . и git diff --check — passed. openspec validate fix-extcheck-location --strict — passed.
- Платформа 1С не запускалась: изменение ограничено Python-сравнением уже разобранных диагностик. Независимое ревью выполняет оркестратор.

## Context sources
Требования к точности позиции и код extended_check.compare. Runtime платформы не проверяется.
