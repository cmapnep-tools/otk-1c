"""Журнал регистрации файловой ИБ (формат .lgf/.lgp): ошибки _$PerformError$_ уровня E."""
import re
from pathlib import Path

_RECORD = re.compile(r"\{(\d{14}),\w,\s*\{[^}]*\},\d+,\d+,\d+,\d+,(\d+),E,\"((?:[^\"]|\"\")*)\"")


def perform_errors(ib):
    """Все ошибки _$PerformError$_ журнала (по порядку). Сравнивать по числу записей до старта."""
    log_dir = Path(ib) / "1Cv8Log"
    lgf = log_dir / "1Cv8.lgf"
    if not lgf.exists():
        return []
    m = re.search(r'\{4,"_\$PerformError\$_",(\d+)\}', lgf.read_text(encoding="utf-8-sig", errors="replace"))
    if not m:
        return []
    code, found = m.group(1), []
    for lgp in sorted(log_dir.glob("*.lgp")):
        text = lgp.read_text(encoding="utf-8-sig", errors="replace")
        found += [e.group(3).replace('""', '"') for e in _RECORD.finditer(text) if e.group(2) == code]
    return found
