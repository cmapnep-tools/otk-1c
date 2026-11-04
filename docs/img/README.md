# Картинки README и сайта

## Логотип и превью

| Файл | Что | Как получен |
| --- | --- | --- |
| `logo.svg` | штамп ОТК с зернистостью оттиска (README, превью) | `logo_svg.py` |
| `logo-flat.svg` | тот же штамп ровной заливкой (шапка и значок вкладки сайта) | `logo_svg.py --flat` |
| `preview.png` | превью репозитория для соцсетей, 1280×640 (Settings → Social preview) | `preview.html`, Edge без окна |

Буквы логотипа — контуры шрифта Roboto Bold (Apache-2.0), поэтому SVG не зависит от шрифтов у зрителя:

```bash
pip install fonttools
python logo_svg.py Roboto-Bold.ttf logo.svg
python logo_svg.py Roboto-Bold.ttf logo-flat.svg --flat
msedge --headless=new --hide-scrollbars --window-size=1280,640 --screenshot=preview.png file:///…/preview.html
```

## Демо и сводка

Сняты с настоящего прогона на ветке `defects` демо-проекта
[otk-1c-demo](https://github.com/cmapnep-tools/otk-1c-demo) (8.5.1, режим совместимости 8.3.21).

| Файл | Что | Как получен |
| --- | --- | --- |
| `demo.svg` | анимация терминала: `otk gate --static-only`, затем полный `otk gate` | `terminal_svg.py` |
| `summary-light.png`, `summary-dark.png` | `summary.html` того же прогона, развёрнуты находки запроса и формы | Edge без окна |

Повторить (из клона демо на ветке `defects`, ОТК без установки — `PYTHONPATH=<otk-1c>/src`):

```bash
python terminal_svg.py record s1.json "otk gate --base master --static-only --quiet --problems" \
    -- python -m otk_1c gate --base master --static-only --quiet --problems
python terminal_svg.py record s2.json "otk gate --base master --quiet --problems" \
    -- python -m otk_1c gate --base master --quiet --problems
python terminal_svg.py render demo.svg --title "otk-1c-demo, ветка defects — ускорено: статика 14 с, полная проверка 40 с" \
    --sub 'D:\путь\к\otk-1c-demo\=' --sub 'D:\каталог\прогонов\=%TEMP%\otk\' s1.json s2.json
```

`--sub` убирает пути машины. Паузы дольше 1,5 с анимация сокращает, поэтому реальное время указано в заголовке окна.

Снимки сводки: в копии `summary.html` у развёрнутых находок добавить атрибут `open` к `<details>`, затем

```bash
msedge --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1100,980 \
    --screenshot=summary-light.png file:///…/summary.html
```

Для тёмной темы добавить ключи `--force-dark-mode --blink-settings=preferredColorScheme=0`.
