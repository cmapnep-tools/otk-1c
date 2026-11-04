# ОТК и анализаторы BSL: контрольные дефекты

Таблица сгенерирована командой `otk controls --analyzers --doc docs/vs-analyzers.md`; руками не править. Каждый дефект вносится в отдельный модуль коммитом вне веток, затем — статика `otk gate --static-only` по этому коммиту с обоими анализаторами (`environment.analyzer = "both"`). Анализатор находит только новые замечания — по правке, относительно базовой редакции.

- Конфигурация: Демо: склад (otk-1c) 1.0.0
- Анализаторы: BSL LS — 1.0.7; bsl-analyzer — 0.3.2
- Коммит: `307ceedcc8`, дата прогона: 2026-10-21

Ячейки анализаторов — правила новых замечаний в файлах дефекта и уровень находки ОТК (`error` — вердикт «не пропускать», `warning` — предупреждение), «нет» — замечаний нет, «не проверено» — анализатор не работал. «Статика ОТК» — остальные проверки статики с находкой в файлах дефекта. «Шаг ОТК» — ожидаемый шаг дефекта в `otk controls` (справочно: здесь пробы в базе не выполняются).

| Дефект | Шаг ОТК | BSL LS | bsl-analyzer | Статика ОТК |
| --- | --- | --- | --- | --- |
| синтаксическая ошибка в невызываемой функции серверного модуля | `static:bslls:ParseError`, `run:com`, `run:extcheck` | `ParseError` (error) | `AllFunctionPathMustHaveReturn` (warning), `ParseError` (error), `UnreachableCode` (warning) | — |
| необъявленная переменная | `run:com`, `run:extcheck` | нет | нет | — |
| вызов несуществующей процедуры модуля | `static:platform-api`, `run:com`, `run:extcheck` | нет | `UnresolvedBareCall` (warning) | `platform-api` |
| Справочники.<опечатка> в неисполняемой ветке | `static:metadata-ref`, `run:extcheck` | нет | `UnresolvedField` (warning) | `metadata-ref` |
| несуществующий предопределённый элемент | `static:metadata-ref` | нет | нет | `metadata-ref` |
| Новый для типа платформы без конструктора | `static:platform-api`, `run:com`, `run:extcheck` | нет | нет | `platform-api` |
| удалён экспортный метод с вызывающими | `static:dependent` | нет | нет | `dependent` |
| несуществующая форма в ПолучитьФорму("…") | `static:form-name` | нет | нет | `form-name` |
| метод, недоступный только в веб-клиенте (КомандаСистемы) | `run:extcheck` | нет | `UnavailableInEnvironment` (warning) | `platform-api` |
| Вопрос() в модуле объекта вне #Если [^1] | `run:com` | `ParseError` (error) | `UnavailableInEnvironment` (warning), `UsingModalWindows` (warning) | `platform-api` |
| ошибка в клиентской процедуре модуля формы | `static:bslls:ParseError`, `run:yaxunit` | `ParseError` (error) | `AllFunctionPathMustHaveReturn` (warning), `ParseError` (error), `UnreachableCode` (warning) | — |
| переименован обработчик события формы | `static:handler` | нет | нет | `handler` |
| вызов несуществующей процедуры в модуле формы | `run:extcheck` | нет | `UnresolvedBareCall` (warning) | `platform-api` |
| вызов несуществующей процедуры в модуле объекта [^2] | `run:extcheck` | `ParseError` (error) | `UnresolvedBareCall` (warning) | `platform-api` |
| исключение в обработчике открытия формы | `run:yaxunit` | `UnreachableCode` (warning) | `UnreachableCode` (warning) | — |
| зависание формы при открытии (бесконечный цикл в ПриОткрытии) | `run:yaxunit` | нет | нет | — |
| опечатка в поле запроса | `run:com`, `run:yaxunit` | нет | `UnknownFieldInQuery` (warning) | — |
| лишний параметр в вызове экспортного метода | `static:common-method` | нет | `MismatchedArgCount` (warning) | `common-method` |
| метод, недоступный только в веб-клиенте, в модуле формы | `run:extcheck` | нет | `UnavailableInEnvironment` (warning) | `platform-api` |
| метод более новой линейки платформы, чем режим совместимости: ПолучитьОбщуюПалитруЦветовЗначковТаблицы (8.5.1) | `static:platform-api`, `run:com`, `run:extcheck` | нет | `UnresolvedBareCall` (warning) | `platform-api` |
| метод новее режима совместимости той же линейки: ЗаписатьЗначениеJSON (8.3.23) | `static:platform-api` | нет | `TypeMismatch` (warning) | `platform-api` |
| конструктор новее режима совместимости: ГенераторСлучайныхПаролей (8.3.22) | `static:platform-api` | нет | нет | `platform-api` |
| функция без Возврат на одной из ветвей [^3] | анализаторы | нет | `AllFunctionPathMustHaveReturn` (warning) | — |
| серверный тип в клиентском модуле (Новый Запрос) | анализаторы | нет | `UnavailableInEnvironment` (warning) | `platform-api` |
| метод, которого нет у типа значения (Структура.Добавить) [^4] | анализаторы | нет | нет | — |
| запрос к несуществующей таблице | анализаторы | нет | `QueryToMissingMetadata` (warning) | — |
| модальный вызов в клиентском модуле (Вопрос) | анализаторы | `UsingModalWindows` (warning) | `UnavailableInEnvironment` (warning), `UsingModalWindows` (warning) | — |
| ошибка в модуле сеанса | `static:bslls:ParseError`, `run:yaxunit` | `ParseError` (error) | `AllFunctionPathMustHaveReturn` (warning), `ParseError` (error), `UnreachableCode` (warning) | — |

## Расхождения анализаторов

- вызов несуществующей процедуры модуля: только bsl-analyzer (`UnresolvedBareCall`)
- Справочники.<опечатка> в неисполняемой ветке: только bsl-analyzer (`UnresolvedField`)
- метод, недоступный только в веб-клиенте (КомандаСистемы): только bsl-analyzer (`UnavailableInEnvironment`)
- вызов несуществующей процедуры в модуле формы: только bsl-analyzer (`UnresolvedBareCall`)
- опечатка в поле запроса: только bsl-analyzer (`UnknownFieldInQuery`)
- лишний параметр в вызове экспортного метода: только bsl-analyzer (`MismatchedArgCount`)
- метод, недоступный только в веб-клиенте, в модуле формы: только bsl-analyzer (`UnavailableInEnvironment`)
- метод более новой линейки платформы, чем режим совместимости: ПолучитьОбщуюПалитруЦветовЗначковТаблицы (8.5.1): только bsl-analyzer (`UnresolvedBareCall`)
- метод новее режима совместимости той же линейки: ЗаписатьЗначениеJSON (8.3.23): только bsl-analyzer (`TypeMismatch`)
- функция без Возврат на одной из ветвей: только bsl-analyzer (`AllFunctionPathMustHaveReturn`)
- серверный тип в клиентском модуле (Новый Запрос): только bsl-analyzer (`UnavailableInEnvironment`)
- запрос к несуществующей таблице: только bsl-analyzer (`QueryToMissingMetadata`)

## Вывод

Из 28 дефектов BSL LS поймал 7, bsl-analyzer — 19, статика ОТК без анализаторов — 17; не поймала статика вовсе — 3 (их ловят пробы в базе, extcheck и дымовой тест — `docs/vs-designer.md`). Анализаторы — источник находок внутри ОТК (проверка `analyzers`), а не альтернатива ему: ни один не видит ошибок компиляции в контексте платформы, запросов в базе, открытия форм и сценарных тестов, а замечания до правки ОТК отсекает сравнением редакций.

[^1]: `ParseError` BSL LS — ложный: код дописан после `#КонецЕсли`, а в ветке `#Иначе` стоит оператор тела модуля; BSL LS не учитывает препроцессор, платформа для сервера эту ветку не компилирует
[^2]: `ParseError` BSL LS — ложный, как у `Вопрос()` в модуле объекта
[^3]: `AllFunctionPathMustHaveReturn` BSL LS с параметрами по умолчанию не сообщает о `Если` без `Иначе`
[^4]: ловит только исполнение: компилятор и анализаторы тип значения переменной здесь не выводят
