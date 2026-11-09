"""Erros públicos com códigos estáveis e mensagens sem valores da entrada."""


class ZoneError(ValueError):
    def __init__(self, code: str, message: str, path: str = "") -> None:
        self.code = code
        self.message = message
        self.path = path
        super().__init__(message)
