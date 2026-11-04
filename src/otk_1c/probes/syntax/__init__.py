"""Справка синтакс-помощника установленных платформ 1С: индекс `.hbk` в SQLite (FTS5) с версией появления.

Индекс строится у пользователя из его платформы (кэш — `index.cache_dir()`); данные справки в поставку не входят.
"""
from .index import (  # noqa: F401
    Index,
    add_platform,
    build_command,
    build_from,
    cache_dir,
    cache_root,
    cached_indexes,
    candidates,
    configure,
    installed_platforms,
    locate,
    open_exact,
    open_index,
    status,
    version_key,
)
