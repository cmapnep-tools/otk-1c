"""Только командная строка: аргументы окружения, --set, расширение распознаётся по Configuration.xml."""
import contextlib
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import otk_1c
from _fs import rmtree_force
from otk_1c import cli, config
from otk_1c.errors import InfraError
from test_collect import FLAGS, write

EXT_CONFIG = ('<MetaDataObject><Configuration><Properties><ObjectBelonging>Adopted</ObjectBelonging>'
              '<Name>Расш</Name><ConfigurationExtensionPurpose>AddOn</ConfigurationExtensionPurpose>'
              '<ScriptVariant>Russian</ScriptVariant></Properties>'
              '<ChildObjects><CommonModule>Расш_Модуль</CommonModule></ChildObjects></Configuration></MetaDataObject>')
MAIN_CONFIG = ('<MetaDataObject><Configuration><Properties><Name>Типовая</Name><ScriptVariant>Russian</ScriptVariant>'
               '</Properties><ChildObjects><Catalog>Номенклатура</Catalog></ChildObjects></Configuration></MetaDataObject>')
MODULE = ("Процедура П() Экспорт\n\tА = Справочники.Номенклатура.ПустаяСсылка();\n"
          "\tБ = Справочники.Нет.ПустаяСсылка();\nКонецПроцедуры\n")


def args(**kw):
    return SimpleNamespace(**kw)


class TestVersion(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            cli.main(["--version"])
        self.assertEqual(e.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"otk {otk_1c.__version__}")

    def test_version_matches_pyproject(self):
        """Версия пакета и __version__ меняются вместе (выпуск и версия разработки .dev0)."""
        with open(Path(__file__).resolve().parents[1] / "pyproject.toml", "rb") as f:
            self.assertEqual(tomllib.load(f)["project"]["version"], otk_1c.__version__)

    def test_platform_help_build_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "help"
            source.mkdir()
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(cli.syntax, "build_from",
                                                                    side_effect=AssertionError("build called")):
                code = cli.main(["platform-help", "build", "--repo", tmp, "--from", str(source),
                                 "--version", "8.3.27", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("План platform-help build", out.getvalue())
            self.assertIn("8.3.27.db", out.getvalue())

    def test_availability_build_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"project": {"source": "src/cf"}, "environment": {"platform": "C:/1cv8/bin"}}
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    mock.patch.object(cli.gitdiff, "git", return_value=str(root)), \
                    mock.patch.object(cli.config, "load", return_value=cfg), \
                    mock.patch.object(cli, "platform_version", return_value="8.3.27.2342"), \
                    mock.patch.object(cli.availability, "build", side_effect=AssertionError("build called")):
                code = cli.main(["availability", "build", "--repo", tmp, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("План availability build", out.getvalue())
            self.assertIn("8.3.27.json", out.getvalue())        # как availability.build: три компонента версии
            self.assertNotIn("8.3.27.2342.json", out.getvalue())
            self.assertFalse((root / "8.3.27.2342.json").exists())


class TestApplyArgs(unittest.TestCase):
    def test_priority_and_paths(self):
        cfg = config._merge(config.DEFAULTS, {"environment": {"ib": "из-toml", "user": "toml"}})
        out = config.apply_args(cfg, args(ib="копия", user=None, platform=None, bslls="bsl-language-server",
                                          parent="выгрузка", extension=None,
                                          set=["environment.user=Админ", "checks.analyzers=off", "smoke.timeout=120",
                                               "environment.executors.server=yaxunit", 'scenarios.path="t"']))
        env = out["environment"]
        self.assertEqual(env["ib"], str(Path("копия").resolve()))          # аргумент важнее toml, путь от cwd
        self.assertEqual(env["user"], "Админ")                               # --set тоже важнее toml
        self.assertEqual(env["bslls"], "bsl-language-server")               # имя в PATH — не путь
        self.assertEqual(env["executors"], {"server": "yaxunit"})
        self.assertEqual(out["checks"], {"analyzers": "off"})
        self.assertEqual(out["smoke"]["timeout"], 120)
        self.assertEqual(out["scenarios"]["path"], "t")
        self.assertEqual(out["project"]["parent"], str(Path("выгрузка").resolve()))
        self.assertEqual(cfg["environment"]["ib"], "из-toml")               # исходные настройки не меняются

    def test_set_errors(self):
        for bad in ("checks", "checks.analyzers", ".x=1"):
            with self.assertRaises(ValueError):
                config.apply_args(config.DEFAULTS, args(set=[bad]))

    def test_env_over_files(self):
        """Порядок: аргумент > OTK_* > otk.local.toml > otk.toml; логическая переменная — 0/false/no."""
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "otk.toml").write_text('[environment]\nplatform = "C:/toml"\nib = "C:/toml-ib"\n', encoding="utf-8")
            (tmp / "otk.local.toml").write_text('[environment]\nplatform = "C:/local"\n', encoding="utf-8")
            environ = {"OTK_PLATFORM": "C:/env", "OTK_PASSWORD": "секрет", "OTK_HIDDEN_DESKTOP": "0"}
            cfg = config.load(tmp, environ)
            env = cfg["environment"]
            self.assertEqual((env["platform"], env["ib"], env["password"], env["hiddenDesktop"]),
                             ("C:/env", "C:/toml-ib", "секрет", False))
            self.assertEqual(config.apply_args(cfg, args(platform=str(tmp)))["environment"]["platform"],
                             str(tmp.resolve()))
            self.assertNotIn("password", config.load(tmp, {})["environment"])
        finally:
            rmtree_force(tmp)

    def test_unknown_context_is_config_error(self):
        """Опечатка в контексте — ошибка настройки: иначе Ordinaryclient не совпал бы ни с одним контекстом модуля,
        и обязательное покрытие считалось бы выполненным без проверки."""
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "otk.toml").write_text('[environment]\nrequiredContexts = ["Ordinaryclient"]\n',
                                          encoding="utf-8")
            with self.assertRaises(InfraError) as e:
                config.load(tmp, {})
            self.assertEqual(e.exception.code, "config-invalid")
            self.assertIn("Ordinaryclient", str(e.exception))
            self.assertIn("OrdinaryClient", str(e.exception))        # допустимые значения в сообщении
            (tmp / "otk.toml").write_text('[environment]\nrequiredContexts = ["OrdinaryClient", "Server"]\n',
                                          encoding="utf-8")
            self.assertEqual(config.load(tmp, {})["environment"]["requiredContexts"],
                             ["OrdinaryClient", "Server"])
        finally:
            rmtree_force(tmp)

    def test_contexts_must_be_list_of_names(self):
        """Строка вместо списка итерировалась бы по буквам, пустая строка и веб-клиент не проверяются пробами."""
        for toml in ('[environment]\ncontexts = "Server"\n',
                     '[environment]\nrequiredContexts = [""]\n',
                     '[environment]\nrequiredContexts = ["WebClient"]\n',
                     '[environment]\nrequiredContexts = [1]\n'):
            tmp = Path(tempfile.mkdtemp())
            try:
                (tmp / "otk.toml").write_text(toml, encoding="utf-8")
                with self.assertRaises(InfraError) as e:
                    config.load(tmp, {})
                self.assertEqual(e.exception.code, "config-invalid")
            finally:
                rmtree_force(tmp)

    def test_unknown_keys(self):
        cfg = config._merge(config.DEFAULTS, {"smoke": {"exlude": []}, "hooks": {}, "checks": {"x": "on"}})
        self.assertEqual(config.unknown_keys(cfg), ["smoke.exlude", "[hooks]"])
        self.assertEqual(config.unknown_keys(config.DEFAULTS), [])

    def test_compat_warnings_args(self):
        """--min-platform — строка версии, не путь; --no-compat-warnings — environment.compatWarnings = false."""
        env = config.apply_args(config.DEFAULTS, args(min_platform="8.3.24", no_compat_warnings=True))["environment"]
        self.assertEqual((env["minPlatform"], env["compatWarnings"]), ("8.3.24", False))
        self.assertNotIn("compatWarnings", config.apply_args(config.DEFAULTS, args())["environment"])


class TestExtensionDetect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        cls.ext, cls.main, cls.work = cls.tmp / "расширение", cls.tmp / "типовая", cls.tmp / "работа"
        write(cls.ext, "Configuration.xml", EXT_CONFIG)
        write(cls.ext, "CommonModules/Расш_Модуль.xml", FLAGS)
        write(cls.ext, "CommonModules/Расш_Модуль/Ext/Module.bsl", MODULE)
        write(cls.main, "Configuration.xml", MAIN_CONFIG)
        cls.work.mkdir()

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def test_extension_name(self):
        self.assertEqual(config.extension_name(self.ext), "Расш")
        self.assertIsNone(config.extension_name(self.main))
        self.assertIsNone(config.extension_name(self.tmp / "нет"))
        cfg = config._merge(config.DEFAULTS, {"project": {"extension": "Своё"}})
        self.assertEqual(config.detect_extension(cfg, self.ext), "Своё")       # настройка важнее

    def gate(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["gate", "--repo", str(self.work), "--dir", str(self.ext), "--run-dir",
                             str(self.tmp / "прогоны"), "--set", "checks.analyzers=off", "--set",
                             "checks.platform-api=off", *extra])
        run_dir = next((self.tmp / "прогоны").iterdir())
        return code, out.getvalue(), json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    def test_gate_extension_without_parent(self):
        """Без --parent ссылки на основную конфигурацию — предупреждения и примечание, а не ошибки."""
        code, out, summary = self.gate()
        self.assertEqual(code, 0)
        self.assertIn("расширение «Расш» — распознано", out)
        self.assertIn(config.NO_PARENT, summary["notes"])
        no_parent = [f for f in summary["findings"] if f["severity"] == "warning" and "нет --parent" in f["message"]]
        self.assertEqual(len(no_parent), 2)
        self.assertIn("probes-server", {i.get("check") for i in summary["notChecked"]})     # без --ib

    def test_gate_extension_with_parent(self):
        code, _, summary = self.gate("--parent", str(self.main))
        self.assertEqual(code, 1)
        self.assertEqual([f["message"] for f in summary["findings"] if f["severity"] == "error"],
                         ["Объект не найден: Справочники.Нет"])
        self.assertNotIn(config.NO_PARENT, summary["notes"])

    def test_doctor_shows_extension(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["doctor", "--repo", str(self.work), "--dir", str(self.ext), "--parent", str(self.main)])
        self.assertIn("расширение «Расш» (распознано по файлу конфигурации)", out.getvalue())
        self.assertIn(f"основная конфигурация: {self.main.resolve()}", out.getvalue())


if __name__ == "__main__":
    unittest.main()
