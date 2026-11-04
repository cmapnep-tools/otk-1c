"""HTML-сводка прогона (summary.html): один файл без внешних зависимостей рядом с summary.md.

Находки разворачиваются (место, метод, источники, строка кода, ссылка на справочник), фильтр по разделам
(ошибки, предупреждения, не проверено, не покрыто) и поиск по тексту. Открыть после прогона — `--open` или
`[report] open` (open_summary); в CI — артефакт задания.
"""
import collections
import os
import re
import shutil
import subprocess
import webbrowser
from html import escape
from pathlib import Path

from . import report

CSS = """
:root{--bg:#fff;--fg:#1d1f23;--muted:#62666d;--line:#d9dce1;--card:#f6f7f9;--err:#c62828;--warn:#a86400;
--skip:#4a5a8a;--ok:#2e7d32;--code:#eef0f3;--link:#1a5fb4}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e4e6ea;--muted:#9aa0a8;--line:#30343b;--card:#1e2126;
--err:#ef6b6b;--warn:#e0a640;--skip:#8fa3e0;--ok:#6cc070;--code:#262a30;--link:#7fb0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-wrap:anywhere}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:20px 0 8px}
h3{font-size:14px;margin:12px 0 6px;color:var(--muted)}
.meta{color:var(--muted)}.v0{color:var(--ok)}.v1{color:var(--err)}.v2{color:var(--warn)}
.bar{position:sticky;top:0;background:var(--bg);padding:8px 0;display:flex;flex-wrap:wrap;gap:6px;
border-bottom:1px solid var(--line);z-index:1}
.bar label{border:1px solid var(--line);border-radius:14px;padding:2px 10px;cursor:pointer;user-select:none}
.bar input[type=search]{flex:1;min-width:160px;padding:3px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg)}
details.item{border:1px solid var(--line);border-left:4px solid var(--line);border-radius:6px;margin:4px 0;
background:var(--card)}
details.item>summary{padding:5px 8px;cursor:pointer;list-style-position:inside}
details.error{border-left-color:var(--err)}details.warning{border-left-color:var(--warn)}
details.skip{border-left-color:var(--skip)}
.body{padding:2px 12px 8px;color:var(--muted)}.body div{margin:2px 0}
code,pre{font:12px/1.4 Consolas,"Cascadia Mono",monospace;background:var(--code);border-radius:4px}
code{padding:0 4px}pre{padding:6px 8px;overflow-x:auto;color:var(--fg);margin:4px 0}
a{color:var(--link)}.tag{font-size:12px;color:var(--muted)}
table{border-collapse:collapse}td,th{border:1px solid var(--line);padding:2px 8px;text-align:left}
.hidden{display:none}
"""

JS = """
const q=document.getElementById('q');
function apply(){const t=q.value.trim().toLowerCase();
document.querySelectorAll('section[data-kind]').forEach(s=>{
s.classList.toggle('hidden',!document.getElementById('f-'+s.dataset.kind).checked);
s.querySelectorAll('details.item').forEach(d=>d.classList.toggle('hidden',t&&!d.textContent.toLowerCase().includes(t)));});}
document.querySelectorAll('.bar input').forEach(i=>i.addEventListener('input',apply));
"""


def _e(text):
    return escape(str(text), quote=True)


def _md(text):
    """Текст с `кодом` из markdown-сводки -> экранированный HTML с <code>."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", _e(text))


def _finding(f):
    head = f"<code>{_e(report.finding_place(f))}</code>" + (f" {_e(f['method'])}" if f.get("method") else "")
    count = f" ×{f['count']}" if f.get("count") else ""
    rows = [f"<div>найдено: {_e(report.sources_text(f))}</div>"]
    if report.where(f):
        rows.append(f"<div>где: <code>{_e(report.where(f))}</code></div>")
    if f.get("place") and f.get("file"):
        rows.append(f"<div>файл: <code>{_e(f['file'])}:{_e(f['line'])}</code></div>")
    if f.get("text"):
        rows.append(f"<pre>{_e(f['text'])}</pre>")
    rows.append(f'<div>код: <a href="{_e(report.doc_link(f["rule"]))}">{_e(f["rule"])}</a>'
                ' — что значит и что делать</div>')
    return (f'<details class="item {_e(f["severity"])}"><summary>{head} — {_md(f["message"])}'
            f' <span class="tag">[{_e(f["rule"])}]{count}</span></summary>'
            f'<div class="body">{"".join(rows)}</div></details>')


def _skip(title, body):
    return f'<details class="item skip"><summary>{title}</summary><div class="body">{body}</div></details>'


def html(summary):
    errors = [f for f in summary["findings"] if f["severity"] == "error"]
    warnings = [f for f in summary["findings"] if f["severity"] == "warning"]
    span = f"{summary['base']}..{summary['head']}" if summary["base"] else f"{summary['head']} (все модули)"
    md = report.markdown(summary).splitlines()
    # строки о сценариях, формах и Конфигураторе — те же, что в summary.md (между заголовком и «## Ошибки»)
    extra = [line for line in md[3:md.index(f"## Ошибки ({len(errors)})")] if line.strip()]
    out = ["<!doctype html><html lang=ru><head><meta charset=utf-8>",
           '<meta name=viewport content="width=device-width,initial-scale=1">',
           f"<title>ОТК: {_e(summary['verdict'])}</title><style>{CSS}</style></head><body><main>",
           f'<h1 class="v{_e(summary["code"])}">ОТК: {_e(summary["verdict"])} (код {_e(summary["code"])})</h1>',
           f'<div class="meta"><code>{_e(span)}</code> — модулей: {_e(summary["modules"])}, '
           f'запросов: {_e(summary["queries"])}</div>']
    out += [f'<div class="meta">{_md(line)}</div>' for line in extra]
    sections = [("errors", "Ошибки", len(errors)), ("warnings", "Предупреждения", len(warnings)),
                ("notchecked", "Не проверено", len(summary["notChecked"])),
                ("uncovered", "Не покрыто", len(summary["uncovered"]))]
    out.append('<div class="bar">' + "".join(
        f'<label><input type=checkbox id="f-{k}" checked> {t} ({n})</label>' for k, t, n in sections)
        + '<input type=search id=q placeholder="поиск: модуль, метод, код, текст"></div>')

    out.append(f'<section data-kind="errors"><h2>Ошибки ({len(errors)})</h2>')
    groups = report.repeated(errors)
    if groups:
        out.append(f"<h3>Частые причины ({len(groups)})</h3><ul>" + "".join(
            f'<li><a href="{_e(report.doc_link(c))}"><code>{_e(c)}</code></a> {_e(m)} — мест: {n}, модулей: {k}</li>'
            for c, m, n, k in groups) + "</ul>")
    by_object = collections.defaultdict(list)
    for f in errors:
        by_object[f.get("object") or f.get("file") or "без модуля"].append(f)
    for obj, items in by_object.items():
        out.append(f"<h3>{_e(obj)}</h3>" + "".join(_finding(f) for f in items))
    out.append("" if errors else "<p>нет</p>")
    out.append(f'</section><section data-kind="warnings"><h2>Предупреждения ({len(warnings)})</h2>')
    by_code = collections.defaultdict(list)
    for f in warnings:
        by_code[f["rule"]].append(f)
    for code_, items in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
        if len(items) > 3:      # как в summary.md: частые коды — группой
            out.append(f'<h3><a href="{_e(report.doc_link(code_))}">{_e(code_)}</a> ×{len(items)}</h3>')
        out += [_finding(f) for f in items]
    out.append("" if warnings else "<p>нет</p>")

    out.append(f'</section><section data-kind="notchecked"><h2>Не проверено ({len(summary["notChecked"])})</h2>')
    for i in summary["notChecked"]:
        where = f" <code>{_e(i['file'])}:{_e(i['line'])}</code>" if i.get("file") else ""
        body = _md(i["reason"]) + (f"<div>закроет: {_e(report.needs_text(i['needs']))}</div>" if i.get("needs") else "")
        out.append(_skip(_e(i["what"]) + where, body))
    out.append("" if summary["notChecked"] else "<p>нет</p>")
    out.append(f'</section><section data-kind="uncovered">'
               f'<h2>Не покрыто этим прогоном ({len(summary["uncovered"])})</h2>')
    groups = collections.defaultdict(list)
    for i in summary["uncovered"]:
        groups[i["needs"].partition(":")[0]].append(i)
    for group, items in groups.items():
        title, hint = report.NEEDS_TITLES.get(group, (group, ""))
        out.append(f"<h3>{_e(title)}" + (f" — {_md(hint)}" if hint else "") + "</h3>")
        out += [_skip(f"{_e(i['owner'])} [{_e(report.CONTEXT_NAMES.get(i['context'], i['context']))}]",
                      _md(i["reason"])) for i in items]
    out.append(("" if summary["uncovered"] else "<p>нет</p>") + "</section>")

    if summary["notes"]:
        out.append("<h2>Примечания</h2><ul>" + "".join(f"<li>{_md(n)}</li>" for n in summary["notes"]) + "</ul>")
    if summary["timings"]:
        out.append("<h2>Время, с</h2><table><tr><th>Шаг</th><th>Время</th></tr>"
                   + "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in summary["timings"].items())
                   + "</table>")
    out.append(f"</main><script>{JS}</script></body></html>")
    return "\n".join(out) + "\n"


OPEN_KINDS = ("html", "md")


def open_summary(run_dir, kind="html", log=print):
    """Открыть сводку: html — в браузере по умолчанию; md — в VS Code (code -r), если есть, иначе системным
    просмотром. В CI (переменная CI) не открывается."""
    if os.environ.get("CI"):
        return
    path = Path(run_dir).resolve() / ("summary.md" if kind == "md" else "summary.html")
    if not path.exists():
        log(f"сводки нет: {path}")
        return
    try:
        if kind == "md" and shutil.which("code"):
            subprocess.run(["code", "-r", str(path)], shell=os.name == "nt", stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        else:
            webbrowser.open(path.as_uri())
    except Exception as e:  # noqa: BLE001 — не открылось: путь к сводке напечатан, вердикт не меняется
        log(f"сводка не открылась: {e}")
