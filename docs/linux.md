# Linux

ОТК на Linux проверяет то же, что на Windows, кроме внешнего соединения: COM на Linux нет, серверные пробы и
разбор запросов идут через [YAxUnit](https://github.com/bia-technologies/yaxunit) в тонком клиенте. Проверено на WSL2 (Ubuntu 24.04, Python 3.12, платформа
8.3.27.2342, лицензия для разработчиков): `doctor`, `gate` с пробами и `controls --parity` на демо-проекте дают тот
же результат, что на Windows. В контейнере Docker — ещё нет.

## Что отличается от Windows

| | Windows | Linux |
| --- | --- | --- |
| Внешнее соединение (COM) | проба через COM | «не проверено» с причиной; серверный код и запросы — через YAxUnit, код под `#Если ВнешнееСоединение` проверяет только полная проверка Конфигуратором |
| Окна 1С | клиент — на скрытом рабочем столе | Конфигуратор и клиенты — на своём Xvfb ОТК |
| Потомки клиента после таймаута | завершаются вместе с объектом задания | завершаются вместе с группой процессов |
| База занята | по блокировке `.cfl` до запуска | по ответу Конфигуратора («Ошибка блокировки информационной базы»): платформа на Linux держит блокировку не в `.cfl` |

## Установка ОТК

В Ubuntu 24.04 Python 3.12 есть, а pip и venv — нет (`No module named pip`). Проще всего — pipx:

```
sudo apt install pipx
pipx install otk-1c
pipx ensurepath        # каталог ~/.local/bin в PATH; затем новый терминал
```

Или своё окружение: `sudo apt install python3-venv`, затем `python3 -m venv ~/otk && ~/otk/bin/pip install otk-1c`.
Без установки — `otk.sh` из скачанного каталога. Зависимость `[com]` на Linux не нужна.

## Дисплей: Xvfb

Конфигуратору и клиенту 1С нужен X-сервер: без него они завершаются сразу («Unable to initialize GTK+»). ОТК
запускает свой Xvfb на время работы и гасит его при выходе — окна не появляются на экране, в CI ничего
оборачивать не нужно (`xvfb-run` не требуется). Нет Xvfb — берётся `DISPLAY` сеанса (окна видны); нет ни того, ни
другого — `doctor` показывает `- display`, пробы недоступны. Видеть окна при отладке — `environment.hiddenDesktop =
false` или `OTK_HIDDEN_DESKTOP=0` (нужен `DISPLAY`).

```
sudo apt install xvfb
```

## Установка платформы (Ubuntu 24.04)

1. Дистрибутив «Технологическая платформа 1С:Предприятия (64-bit) для Linux» (`setup-full-<версия>-x86_64.run`)
   с [releases.1c.ru](https://releases.1c.ru). Отдельной сборки для разработчиков нет.
2. Без диалогов:
   ```
   sudo ./setup-full-8.3.27.2342-x86_64.run --mode unattended \
     --enable-components client_full,client_thin,server,v8_install_deps,ru
   ```
   Компонент `en` установщик 8.3.27 не принимает, хотя показывает его в списке по умолчанию. Платформа — в
   `/opt/1cv8/x86_64/<версия>`, ОТК находит её сама.
3. Зависимости. `v8_install_deps` на 24.04 их не ставит (ошибка вызова apt) — поставьте сами:
   ```
   sudo apt install pkexec zenity x11-utils xvfb
   ```
   Конфигуратор и клиенты 8.3.27 собраны с `webkit2gtk-4.0`, которого в Ubuntu 24.04 нет; без него они не
   запускаются (`ldd /opt/1cv8/x86_64/<версия>/1cv8 | grep 'not found'`). Его можно взять из Ubuntu 22.04 с низким
   приоритетом, чтобы остальные пакеты оттуда не ставились:
   ```
   echo 'deb http://archive.ubuntu.com/ubuntu jammy main universe
   deb http://archive.ubuntu.com/ubuntu jammy-updates main universe
   deb http://security.ubuntu.com/ubuntu jammy-security main universe' | sudo tee /etc/apt/sources.list.d/jammy-webkit.list
   printf 'Package: *\nPin: release n=jammy\nPin-Priority: 100\n\nPackage: *\nPin: release n=jammy-updates\nPin-Priority: 100\n\nPackage: *\nPin: release n=jammy-security\nPin-Priority: 100\n' \
     | sudo tee /etc/apt/preferences.d/jammy-webkit
   sudo apt update && sudo apt install libwebkit2gtk-4.0-37 libjavascriptcoregtk-4.0-18
   ```
   `ibcmd` работает и без этого.
4. Локаль `ru_RU.UTF-8` (`sudo locale-gen ru_RU.UTF-8`): проверено с ней; тексты сообщений платформы ОТК
   разбирает по-русски.

## Лицензия для разработчиков

Активируется в окне Конфигуратора (в WSL — через WSLg): «Активировать комьюнити-лицензию», логин и пароль
[developer.1c.ru](https://developer.1c.ru). Пункт показывается не всегда — что проверить, по
[FAQ портала](https://developer.1c.ru):

- доступ к `users.v8.1c.ru` и `login.1c.ru` по HTTPS. VPN может его закрывать — добавьте адрес в исключения;
  этот же доступ нужен для продления лицензии раз в 7 дней;
- `conf.cfg` платформы (`/opt/1cv8/conf/conf.cfg` и `/opt/1cv8/x86_64/<версия>/conf/conf.cfg`):
  ```
  ExternalResourcesMode=D
  SystemLanguage=ru_RU
  ```
  по умолчанию на Linux `SystemLanguage=System`, и пункта нет;
- лицензия «для всех пользователей» пишется в `/var/1C/licenses` — пользователь должен быть в группе `grp1cv8`
  (`sudo usermod -aG grp1cv8 $USER`), иначе мастер предлагает только «для текущего пользователя».

Если в окне мастера на месте пунктов пустая рамка, запустите его с `WEBKIT_DISABLE_COMPOSITING_MODE=1` и
`WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1`: так было под WSLg. Файл лицензии с другого компьютера не подходит:
лицензия привязана к оборудованию, и WSL считается отдельным компьютером (их не больше трёх на учётную запись).
Ограничения лицензии для CI — [licensing.md](licensing.md).

## Замер

Демо-проект `otk-1c-demo`, ветка `defects` против `master`, WSL2 (8 ГБ), платформа 8.3.27.2342:

| | Linux | Windows |
| --- | --- | --- |
| `gate` с пробами | 5 ошибок и 1 предупреждение; пробы — 32 с (серверные и запрос — YAxUnit) | те же находки; пробы — 32 с (серверные и запрос — COM; платформа 8.5.1) |
| `controls --parity`, время на пакет | 66–74 с (Конфигуратор — 2 с) | 57 с (Конфигуратор — 1 с; платформа 8.5.1) |

Контрольные дефекты пойманы те же, что в [vs-designer.md](vs-designer.md); дефект «метод более новой линейки»
требует платформы 8.5 и на 8.3.27 не строится.
