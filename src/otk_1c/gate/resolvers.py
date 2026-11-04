"""Внешние восстановители собранных запросов (D20).

Встроенное восстановление (queries.extract) — первое; запросы, оставшиеся неполными, получает цепочка команд
`[[queries.resolvers]]` (name, command, timeout) по порядку. Протокол — JSON:
  stdin:  {"version": 1, "repo", "source", "queries": [{"id", "module", "owner", "method", "line", "text", "reason"}],
           "modules": {путь от корня выгрузки: текст проверяемой редакции}}
  stdout: {"queries": [{"id", "text", "rationale"}]} — запроса нет в ответе: не восстановлен.
Восстановленный текст — `complete`, `assembled`, `resolver`, `rationale`: ошибка разбора — предупреждение
(report.query_downgrades). Сбой команды — примечание, запросы остаются «не проверено». Ответы (и «не восстановлен»)
кэшируются в каталоге прогона по восстановителю, модулю, строке и тексту. Без настройки ничего не вызывается.
"""
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from ..sources import bsl

VERSION = 1
DEFAULT_TIMEOUT = 300
GAP = "<?>"


def configured(cfg):
    return list((cfg.get("queries") or {}).get("resolvers") or [])


def _key(resolver, q):
    raw = json.dumps([resolver.get("name"), resolver.get("command"), q["module"], q["line"], q["text"]],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def argv(command):
    """Команда без оболочки: строка — аргументы по правилам ОС (Windows разбирает её сама, POSIX — shlex)."""
    if isinstance(command, str) and os.name != "nt":
        return shlex.split(command)
    return command


def _call(resolver, payload):
    """Ответ команды: {id: (текст, обоснование)}; сбой — RuntimeError с причиной."""
    cmd = argv(resolver["command"])
    try:
        p = subprocess.run(cmd, input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True,
                           timeout=float(resolver.get("timeout", DEFAULT_TIMEOUT)))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"нет ответа за {resolver.get('timeout', DEFAULT_TIMEOUT)} с") from None
    except OSError as e:
        raise RuntimeError(f"команда не запустилась: {e}") from None
    if p.returncode:
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"код выхода {p.returncode} {tail[0]}".rstrip())
    try:
        answer = json.loads(p.stdout.decode("utf-8"))
        items = answer["queries"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("ответ — не JSON с полем queries") from None
    out = {}
    for a in items if isinstance(items, list) else []:
        if isinstance(a, dict) and isinstance(a.get("text"), str) and a["text"].strip() and GAP not in a["text"]:
            out[str(a.get("id"))] = (a["text"], str(a.get("rationale") or ""))
    return out


def resolve(plan, repo, read_head, cache_dir, cfg, log=print):
    """Восстанавливает неполные запросы плана на месте; возвращает примечания сводки."""
    resolvers = configured(cfg)
    pending = [q for q in plan.get("queries", []) if not q["complete"]]
    if not resolvers or not pending:
        return []
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    owners = {m["path"]: m.get("owner") for m in plan.get("modules", [])}
    texts = {}
    notes = []
    for n, resolver in enumerate(resolvers, 1):
        name = resolver.get("name") or f"resolver{n}"
        if not resolver.get("command"):
            notes.append(f"восстановитель «{name}»: не задана команда (command)")
            continue
        answers, ask = {}, []
        for q in pending:
            cached = cache_dir / f"{_key(resolver, q)}.json"
            if cached.exists():
                answers[q["id"]] = json.loads(cached.read_text(encoding="utf-8"))
            else:
                ask.append(q)
        if ask:
            for q in ask:
                if q["module"] not in texts:
                    texts[q["module"]] = read_head(q["module"]) or ""
            items = []
            for q in ask:
                method = bsl.parse(texts[q["module"]]).method_at(q["line"])
                items.append({"id": q["id"], "module": q["module"], "owner": owners.get(q["module"]),
                              "method": method.name if method else None, "line": q["line"], "text": q["text"],
                              "reason": q.get("reason")})
            payload = {"version": VERSION, "repo": str(Path(repo).resolve()),
                       "source": plan.get("headDir") or str((Path(repo) / plan["source"]).resolve()),
                       "queries": items, "modules": {m: texts[m] for m in sorted({q["module"] for q in ask})}}
            try:
                got = _call(resolver, payload)
            except RuntimeError as e:
                notes.append(f"восстановитель «{name}»: {e} — запросов не восстановлено: {len(ask)}")
                got = None
            if got is not None:
                for q in ask:
                    hit = got.get(q["id"])
                    value = {"text": hit[0], "rationale": hit[1]} if hit else None
                    (cache_dir / f"{_key(resolver, q)}.json").write_text(json.dumps(value, ensure_ascii=False),
                                                                        encoding="utf-8")
                    answers[q["id"]] = value
        done = 0
        for q in pending:
            a = answers.get(q["id"])
            if a:
                q.update(text=a["text"], complete=True, assembled=True, resolver=name, rationale=a["rationale"])
                q.pop("reason", None)
                done += 1
        if done:
            log(f"восстановитель «{name}»: запросов восстановлено {done} из {len(pending)}")
        pending = [q for q in pending if not q["complete"]]
        if not pending:
            break
    return notes
