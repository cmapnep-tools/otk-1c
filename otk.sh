#!/bin/sh
# ОТК без установки пакета: ./otk.sh gate --dir <выгрузка> ...  (нужен Python 3.11+;
# другой интерпретатор — переменной PYTHON)
here=$(cd "$(dirname "$0")" && pwd)
py=${PYTHON:-}
if [ -z "$py" ]; then
    for c in python3 python; do
        # python3 в Windows бывает заглушкой Магазина — проверяем, что интерпретатор работает
        if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then py=$c; break; fi
    done
fi
[ -n "$py" ] || { echo "otk: нужен Python 3.11+ (или переменная PYTHON)" >&2; exit 2; }
sep=":"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) sep=";" ;; esac
PYTHONPATH="$here/src${PYTHONPATH:+$sep$PYTHONPATH}" exec "$py" -m otk_1c "$@"
