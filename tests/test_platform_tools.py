"""Хуки Platform Tools: соединение из аргументов vrunner и профиля, сводка отката."""
import json
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.integrations import platform_tools


class PtArgsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_settings_profile(self):
        """Platform Tools передаёт профиль через --settings; ключи профиля — без «--»."""
        (self.tmp / "autumn-properties.user.json").write_text(json.dumps({"vrunner": {
            "ibconnection": r"/FC:\bases\Демо", "db-user": "Администратор", "db-pwd": "x", "v8version": "8.3.27.2342"}},
            ensure_ascii=False), encoding="utf-8")
        c = platform_tools.parse_args("compileext src/cfe/Demo demo --settings autumn-properties.user.json", self.tmp)
        self.assertEqual(c["ibconnection"], r"/FC:\bases\Демо")
        self.assertEqual(c["db-user"], "Администратор")
        self.assertEqual(c["v8version"], "8.3.27.2342")
        self.assertEqual(c["extension"], "demo")
        self.assertFalse(c["updatedb"])

    def test_explicit_keys_win(self):
        (self.tmp / "env.json").write_text(json.dumps({"default": {"--ibconnection": "/Fпрофиль", "--db-user": "А"}},
                                                      ensure_ascii=False), encoding="utf-8")
        args = r'compileext src ext --ibconnection "/Sсервер\база" --settings env.json --updatedb'
        c = platform_tools.parse_args(args, self.tmp)
        self.assertEqual(c["ibconnection"], r"/Sсервер\база")    # явный ключ главнее профиля
        self.assertEqual(c["db-user"], "А")                     # недостающее — из профиля vrunner 2
        self.assertTrue(c["updatedb"])

    def test_vrunner3(self):
        """vanessa-runner 3: обновление БД по умолчанию, «Только загрузить» — --no-update-db."""
        c = platform_tools.parse_args(r"cfe load --extension-name demo --ibconnection /FC:\Демо src/cfe/Demo", self.tmp)
        self.assertTrue(c["updatedb"])
        self.assertEqual(c["extension"], "demo")
        c = platform_tools.parse_args(r"cfe load --extension-name demo --no-update-db --ibconnection /FC:\Демо src",
                                      self.tmp)
        self.assertFalse(c["updatedb"])
        c = platform_tools.parse_args(r"cfe unload --extension-name demo --ibconnection /FC:\Демо out.cfe", self.tmp)
        self.assertFalse(c["updatedb"])

    def test_vrunner3_configuration(self):
        """Основная конфигурация: cf load (обновление БД по умолчанию), infobase update — всегда с обновлением."""
        c = platform_tools.parse_args(r"cf load --ibconnection /FC:\Демо src/cf", self.tmp)
        self.assertTrue(c["updatedb"])
        self.assertNotIn("extension", c)
        args = r"cf load --no-update-db --increment --ibconnection /FC:\Демо src/cf"
        self.assertFalse(platform_tools.parse_args(args,
                                       self.tmp)["updatedb"])
        self.assertTrue(platform_tools.parse_args(r"infobase update --target main --ibconnection /FC:\Демо",
                                                  self.tmp)["updatedb"])

    def test_configuration_hooks_skip_extension_project(self):
        """Команды основной конфигурации у проекта-расширения не проверяются: его код — в расширении."""
        logs = []
        cfg = {"project": {"source": "src/cfe/X", "extension": "x"}, "environment": {}}
        self.assertEqual(platform_tools.cf_pre(self.tmp, cfg, {}, logs.append, open_summary=False), 0)
        self.assertEqual(platform_tools.cf_post(self.tmp, cfg, {"ibconnection": r"/FC:\Демо"}, logs.append,
                                                open_summary=False), 0)
        self.assertTrue(all("пропущена" in m for m in logs))

    def test_no_connection(self):
        c = platform_tools.parse_args("", self.tmp)
        self.assertNotIn("ibconnection", c)
        self.assertEqual(platform_tools.read_profile(self.tmp / "нет.json"), {})

    def test_markdown(self):
        md = platform_tools.markdown(platform_tools.title("post", "demo", "1c-platform-tools.cfe.load"),
                         [{"file": "src/a.bsl", "line": 5, "message": "Ошибка", "step": "designer",
                                      "code": "CheckModules"}], ["заметка"], "расширение возвращено")
        self.assertIn("# Загрузка расширения demo отменена: найдены ошибки", md)
        self.assertIn("`src/a.bsl:5`", md)
        self.assertIn("**расширение возвращено**", md)


class PtTitleTest(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(platform_tools.title("post", "demo", "1c-platform-tools.cfe.load"),
                         "Загрузка расширения demo отменена: найдены ошибки")
        self.assertEqual(platform_tools.title("check", "demo", "1c-platform-tools.cfe.updateDb"),
                         "Обновление расширения demo в ИБ отменено: найдены ошибки")
        self.assertEqual(platform_tools.title("check", "demo", "1c-platform-tools.cfe.unload"),
                         "Выгрузка расширения demo в *.cfe отменена: найдены ошибки")
        self.assertEqual(platform_tools.title("static", "demo", "1c-platform-tools.cfe.compile"),
                         "Сборка *.cfe расширения demo отменена: найдены ошибки")
        self.assertIn("отменена", platform_tools.title("post", "x"))          # команда неизвестна — по действию
        self.assertEqual(platform_tools.title("cf-pre", "", "1c-platform-tools.cf.load"),
                         "Загрузка конфигурации отменена: найдены ошибки")
        self.assertEqual(platform_tools.title("cf-pre", "", "1c-platform-tools.infobase.updateDb"),
                         "Обновление конфигурации в ИБ отменено: найдены ошибки")


if __name__ == "__main__":
    unittest.main()
