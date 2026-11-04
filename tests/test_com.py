"""COM: сбой подключения не из-за кода — обход через тонкий клиент; ошибка компиляции при подключении — находка."""
import unittest
from unittest import mock

try:
    import pythoncom
    import win32com.client  # noqa: F401
except ImportError:          # pragma: no cover - не Windows
    pythoncom = None

from otk_1c.probes.executors import com


class FakePlatform:
    user, password, ib = "Администратор", "", "D:/ib"

    def unlock(self):
        pass

    def com_connection(self):
        return 'File="D:/ib";'


def connector_failing(text):
    class Connector:
        def Connect(self, _):
            raise pythoncom.com_error(-2147352567, "Ошибка", (0, "V83.COMConnector.1", text, None, 0, -2147467259),
                                      None)
    return Connector()


@unittest.skipUnless(pythoncom, "нужен pywin32")
class ComConnectTest(unittest.TestCase):
    def test_version_mismatch_is_unavailable(self):
        failing = connector_failing("Несоответствие версий клиента и сервера")
        with mock.patch("win32com.client.Dispatch", return_value=failing):
            with self.assertRaises(com.Unavailable) as c:
                com.run(FakePlatform(), {}, [], [], "V83.COMConnector")
        self.assertIn("Несоответствие версий", str(c.exception))

    def test_compile_error_on_connect_is_finding(self):
        text = "{ОбщийМодуль.Глобальный.Модуль(12,5)}: Переменная не определена (Х)"
        with mock.patch("win32com.client.Dispatch", return_value=connector_failing(text)):
            res, note = com.run(FakePlatform(), {}, [], [], "V83.COMConnector")
        self.assertEqual((res[0]["kind"], res[0]["outcome"], res[0]["line"]), ("session", "compile-error", 12))


if __name__ == "__main__":
    unittest.main()
