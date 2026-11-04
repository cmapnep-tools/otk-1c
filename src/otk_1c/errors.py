"""Исключения, общие для всех слоёв."""


class InfraError(Exception):
    """Сбой инфраструктуры (код 2): база занята, платформа не запустилась и т.п. code — код причины
    (gate/reasons.CODES), если его знает место сбоя; None — по тексту сообщения внешней программы."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
