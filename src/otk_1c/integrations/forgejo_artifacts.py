"""Артефакты задания Forgejo Actions без node: загрузка по протоколу actions/upload-artifact v3.

Шаблон Forgejo (docs/forgejo) рассчитан на раннер на машине с 1С, где node может не быть, а действия
JavaScript без него недоступны. Протокол v3 Forgejo принимает (проверено на Forgejo); GitHub его закрыл —
там артефакты собирает --artifacts и грузит actions/upload-artifact@v4. Нет переменных ACTIONS_RUNTIME_* —
загрузка пропускается с предупреждением, сводка остаётся в журнале задания.
"""
import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path


def upload(name, files, environ=None, log=print):
    """Загрузка файлов артефактом name. Возвращает число загруженных файлов."""
    environ = os.environ if environ is None else environ
    base, token, run_id = (environ.get("ACTIONS_RUNTIME_URL"), environ.get("ACTIONS_RUNTIME_TOKEN"),
                           environ.get("GITHUB_RUN_ID"))
    files = [Path(f) for f in files if Path(f).is_file()]
    if not (base and token and run_id):
        log("артефакты: нет ACTIONS_RUNTIME_URL/TOKEN или GITHUB_RUN_ID — загрузка пропущена")
        return 0
    if not base.startswith(("http://", "https://")):
        log(f"артефакты: ACTIONS_RUNTIME_URL не http(s): {base} — загрузка пропущена")
        return 0
    api = f"{base.rstrip('/')}/_apis/pipelines/workflows/{run_id}/artifacts?api-version=6.0-preview"
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/json;api-version=6.0-preview"}

    def call(method, url, data=None, headers=None):
        # адрес — от ACTIONS_RUNTIME_URL раннера, схема проверена выше
        req = urllib.request.Request(url, data=data, method=method, headers={**auth, **(headers or {})})  # noqa: S310
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
            raw = r.read()
            return json.loads(raw) if raw else None

    created = call("POST", api, json.dumps({"Type": "actions_storage", "Name": name}).encode(),
                   {"Content-Type": "application/json"})
    container = created["fileContainerResourceUrl"]
    total = 0
    for f in files:
        data = f.read_bytes()
        item = urllib.parse.quote(f"{name}/{f.name}", safe="")   # как encodeURIComponent в upload-artifact
        sep = "&" if "?" in container else "?"
        call("PUT", f"{container}{sep}itemPath={item}", data,
             {"Content-Type": "application/octet-stream",
              "Content-Range": f"bytes 0-{max(len(data) - 1, 0)}/{len(data)}",
              # Forgejo сверяет контрольную сумму части: без неё 500 «md5 not match»
              "x-actions-results-md5": base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()})
        total += len(data)
    call("PATCH", f"{api}&artifactName={urllib.parse.quote(name)}", json.dumps({"Size": total}).encode(),
         {"Content-Type": "application/json"})
    log(f"артефакт {name}: {', '.join(f.name for f in files)}")
    return len(files)
