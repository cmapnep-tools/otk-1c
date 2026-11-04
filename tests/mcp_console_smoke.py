"""Смоук MCP консоли на настоящей копии ИБ (не юнит-тест, нужна база и COM):
python tests/mcp_console_smoke.py <псевдоним базы> [объект метаданных]"""
import json
import os
import subprocess
import sys
import time

base = sys.argv[1]
obj = sys.argv[2] if len(sys.argv) > 2 else "Справочники"
src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
p = subprocess.Popen([sys.executable, "-m", "otk_1c", "mcp", "--idle", "5"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": src})


def rpc(i, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}},
                             ensure_ascii=False) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


def tool(i, name, args):
    t = time.time()
    r = rpc(i, "tools/call", {"name": name, "arguments": args})["result"]
    return json.loads(r["content"][0]["text"]), r["isError"], round(time.time() - t, 2)


sys.stdout.reconfigure(encoding="utf-8")
print(rpc(1, "initialize")["result"]["serverInfo"])
print([t["name"] for t in rpc(2, "tools/list")["result"]["tools"]])
r, err, sec = tool(3, "ib_check", {"base": base, "text": "ВЫБРАТЬ 1 КАК А ГДЕ &П"})
print("check", r, err, sec)
r, err, sec = tool(4, "ib_query", {"base": base, "text": "ВЫБРАТЬ 1 КАК А", "limit": 5})
print("query (соединение уже есть)", r["results"][0]["rows"], err, sec)
r, err, sec = tool(5, "ib_metadata", {"base": base, "name": obj})
print("metadata", str(r)[:200], err, sec)
print("connected:", tool(6, "ib_bases", {})[0]["bases"].get(base, {}).get("connected"))
time.sleep(22)                                     # простой > idle: соединение должно быть отпущено
print("after idle connected:", tool(7, "ib_bases", {})[0]["bases"].get(base, {}).get("connected"))
conn_str = tool(9, "ib_bases", {})[0]["bases"].get(base, {}).get("connection", "")
if conn_str[:2].upper() == "/F":                   # файловая база свободна: .cfl не удерживает сеанс 1С
    import glob
    for f in glob.glob(os.path.join(conn_str[2:], "*.cfl")):
        try:                                       # открытие на запись блокировку не видит (1С блокирует участки)
            os.rename(f, f + ".probe")
            os.rename(f + ".probe", f)
            state = "free"
        except OSError:
            state = "LOCKED"
        print("  ", os.path.basename(f), state)
r, err, sec = tool(8, "ib_query", {"base": base, "text": "ВЫБРАТЬ Т.Нет ИЗ Справочник.Номенклатура КАК Т"})
print("error", r, err, sec)
p.stdin.close()
p.wait(30)
