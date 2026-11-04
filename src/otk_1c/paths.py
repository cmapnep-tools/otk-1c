"""Каталоги пользователя, общие для всех слоёв."""
import os


def cache_root() -> str:
    """Кэш ОТК пользователя: OTK_CACHE, иначе %LOCALAPPDATA%/otk или ~/.cache/otk."""
    d = os.environ.get("OTK_CACHE")
    if d:
        return d
    base = (os.environ.get("LOCALAPPDATA") if os.name == "nt"
            else os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"))
    return os.path.join(base or os.path.expanduser("~"), "otk")
