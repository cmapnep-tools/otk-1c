"""Режим «рабочая база пользователя» (задача 5.7): полная проверка без отдельной копии ИБ.

Задаётся `environment.connection` (`/F<каталог>` или `/S<сервер>\\<база>`) вместо `environment.ib` (своя копия,
которую ОТК меняет свободно). Копии больших конфигураций дороги, а без удалённого CI проверять приходится
там, где работает разработчик, поэтому база после проверки возвращается к прежнему виду:

- каждое расширение, которое ОТК ставит (проверяемое расширение проекта, YAxUnit, расширение проб,
  тестовые расширения), сначала сохраняется в .cfe, если в базе оно уже было; после проверки — возвращается
  из .cfe, а новое — удаляется. `environment.keepTools = true` оставляет инструменты (быстрее следующий раз);
- основная конфигурация по умолчанию считается уже загруженной (загружать ERP целиком на каждую проверку
  долго); `environment.loadProject = true` — загружать проверяемую редакцию (без возврата);
- база занята (открыт Конфигуратор или сеанс) — проверка не начинается (как и применение изменений в
  «1C: Platform Tools»).
"""
from pathlib import Path

from ..errors import InfraError
from ..sources import layout
from . import build, ib_tools


class Session:
    def __init__(self, platform, run_dir, keep_tools=False, log=print):
        self.platform = platform
        self.dir = Path(run_dir) / "userbase"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep_tools = keep_tools
        self.log = log
        self.guarded = []            # [(имя, было ли в базе, .cfe, инструмент ли)] в порядке установки
        self.unrestored = []         # что последний restore() не вернул: непусто — база изменена (код 2)

    def guard(self, name, tool=True):
        """Запомнить состояние расширения до установки (один раз за сеанс)."""
        if any(g[0].lower() == name.lower() for g in self.guarded):
            return
        backup = self.dir / f"{name}.cfe"
        backup.unlink(missing_ok=True)
        rc, _, out = self.platform.save_extension(name, backup, label=f"ub-save-{name}")
        if rc == 0 and backup.exists():
            existed = True
        elif "не найден" in out.lower() or "not found" in out.lower():
            existed = False
        else:
            raise InfraError(f"не удалось сохранить расширение {name} рабочей базы: {' '.join(out.split())[-300:]}",
                             code="platform-failed")
        self.guarded.append((name, existed, backup, tool))

    def load_project_extension(self, dump, name):
        """Загрузить проверяемую редакцию расширения проекта (прежнее — сохранено). LoadError — ошибки файлов."""
        self.guard(name, tool=False)
        src_layout = layout.detect(dump)
        dump, _ = build.loadable(self.platform, self.platform.run_dir, dump)     # исходники EDT — через экспорт
        rc, _, out = self.platform.load_dir(dump, extension=name, label="ub-load")
        if rc:
            errors = build.source_errors(out, dump, src_layout)
            if errors:
                raise build.LoadError(errors)
            raise InfraError(f"загрузка расширения {name}: {' '.join(out.split())[-300:]}", code="platform-failed")
        rc, _, out = self.platform.update_db(extension=name, label="ub-update")
        if rc:
            raise InfraError(f"обновление БД расширением {name}: {' '.join(out.split())[-300:]}",
                             code="platform-failed")
        return {"action": "user-base", "extension": name}

    def install_tools(self, yaxunit_cfe, config_root, renames=()):
        self.guard(ib_tools.YAX_NAME)
        self.guard(ib_tools.EXT)
        info = ib_tools.install(self.platform, self.dir, yaxunit_cfe, config_root, renames)
        return f"установлены на время проверки (переименования: {', '.join(info['renames']) or 'нет'})"

    def restore(self):
        """Вернуть базу: расширения из .cfe, новые — удалить (инструменты — если не keepTools). Текст итога."""
        done, failed = [], []
        for name, existed, backup, tool in reversed(self.guarded):
            if tool and self.keep_tools:
                continue
            if existed:
                rc, _, out = self.platform.load_extension(name, backup, label=f"ub-restore-{name}")
                if rc == 0:
                    rc, _, out = self.platform.update_db(extension=name, label=f"ub-restore-db-{name}")
                action = "возвращено"
            else:
                rc, _, out = self.platform.delete_extension(name, label=f"ub-delete-{name}")
                action = "удалено"
            if rc:
                failed.append(f"{name}: {' '.join(out.split())[-200:]} (сохранено в {backup})")
            else:
                done.append(f"{name} {action}")
        self.guarded = [g for g in self.guarded if g[3] and self.keep_tools]
        self.unrestored = failed
        if failed:
            return "РАБОЧАЯ БАЗА НЕ ВОССТАНОВЛЕНА ПОЛНОСТЬЮ: " + "; ".join(failed)
        return "рабочая база восстановлена: " + (", ".join(done) or "изменений не было") + \
            ("; инструменты оставлены (keepTools)" if self.keep_tools else "")
