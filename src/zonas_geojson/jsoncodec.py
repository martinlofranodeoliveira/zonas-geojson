"""JSON estrito, limitado e decimal exato, sem alterar o contexto Decimal."""
from __future__ import annotations

import json
import re
from decimal import Decimal

from .errors import ZoneError

MAX_BYTES = 8_388_608
MAX_DEPTH = 16
MAX_NODES = 200_000
MAX_NUMBER_CHARS = 64
MAX_DIGITS = 40
MAX_EXPONENT = 24
MAX_OUTPUT_BYTES = 67_108_864
MAX_OUTPUT_NODES = 1_000_000
_NUMBER = re.compile(r"(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?\Z")


def _error(message: str, code: str = "JSON_INVALID") -> ZoneError:
    return ZoneError(code, message)


def _number(token: str, integer: bool = False):
    match = _NUMBER.fullmatch(token)
    if match is None:
        raise _error("Número JSON inválido.")
    coefficient = match[2] + (match[3] or "")
    if len(token) > MAX_NUMBER_CHARS or len(coefficient) > MAX_DIGITS:
        raise _error("Número excede o limite de dígitos.", "JSON_LIMIT")
    exponent = match[4]
    if exponent is not None and (len(exponent.lstrip("+-0")) > 2 or abs(int(exponent)) > MAX_EXPONENT):
        raise _error("Expoente excede o limite permitido.", "JSON_LIMIT")
    return int(token) if integer else Decimal(token)


def _reject_constant(_value):
    raise _error("Constantes não finitas não são JSON válido.")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _error("Objeto JSON contém chave duplicada.", "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _string(value: str) -> str:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _error("Texto contém Unicode inválido.")
    return json.dumps(value, ensure_ascii=False)


def number_text(value: int | Decimal) -> str:
    """Representação decimal exata para JSON/CSV (sem arredondamento)."""
    if type(value) is int:
        if abs(value) >= 10 ** MAX_DIGITS:
            raise _error("Número excede o limite de dígitos.", "JSON_LIMIT")
        return str(value)
    if type(value) is not Decimal or not value.is_finite():
        raise _error("Tipo numérico não suportado.")
    sign, digits, exponent = value.as_tuple()
    if not any(digits):
        return "0"
    # Construção por tupla: nem normalize(), nem operações sujeitas ao contexto.
    coefficient = ''.join(map(str, digits)).rstrip("0")
    exponent += len(digits) - len(coefficient)
    if len(coefficient) > MAX_DIGITS or abs(exponent) > MAX_DIGITS + MAX_EXPONENT:
        raise _error("Número excede os limites permitidos.", "JSON_LIMIT")
    # Prefere forma fixa; se necessário desloca um expoente explícito limitado.
    # Assim valores de lexemas como 0.00000000000000000001e-24 continuam exatos.
    for explicit in [0] + [v for n in range(1, MAX_EXPONENT + 1) for v in (n, -n)]:
        shift = exponent - explicit
        if shift >= 0:
            mantissa = coefficient + "0" * shift
        else:
            position = len(coefficient) + shift
            mantissa = (coefficient[:position] + "." + coefficient[position:]) if position > 0 else "0." + "0" * -position + coefficient
        token = ("-" if sign else "") + mantissa + ("e" + str(explicit) if explicit else "")
        if len(token) <= MAX_NUMBER_CHARS and sum(c.isdigit() for c in mantissa) <= MAX_DIGITS:
            return token
    raise _error("Número excede os limites permitidos.", "JSON_LIMIT")


def _dumps(value, *, max_nodes: int, max_bytes: int) -> str:
    """Implementação comum; limites maiores apenas para documentos derivados."""
    nodes = 0

    def emit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _error("Documento excede o limite de nós.", "JSON_LIMIT")
        kind = type(item)
        if kind is dict or kind is list:
            if depth > MAX_DEPTH:
                raise _error("Documento excede o limite de profundidade.", "JSON_LIMIT")
            if not item:
                return "{}" if kind is dict else "[]"
            indentation = "  " * depth
            if kind is dict:
                rows = []
                for key, child in item.items():
                    if type(key) is not str:
                        raise _error("Chaves JSON devem ser texto.")
                    rows.append(indentation + _string(key) + ": " + emit(child, depth + 1))
                return "{\n" + ",\n".join(rows) + "\n" + "  " * (depth - 1) + "}"
            rows = [indentation + emit(child, depth + 1) for child in item]
            return "[\n" + ",\n".join(rows) + "\n" + "  " * (depth - 1) + "]"
        if kind is str:
            return _string(item)
        if item is None:
            return "null"
        if kind is bool:
            return "true" if item else "false"
        if kind is int or kind is Decimal:
            return number_text(item)
        raise _error("Documento contém tipo não suportado.")

    result = emit(value, 1) + "\n"
    if len(result.encode("utf-8")) > max_bytes:
        raise _error("Documento excede o limite de bytes canônicos.", "JSON_LIMIT")
    return result


def dumps(value) -> str:
    """Serializa JSON de entrada, pretty2+LF, até 8 MiB/200000 nós."""
    return _dumps(value, max_nodes=MAX_NODES, max_bytes=MAX_BYTES)


def dumps_output(value) -> str:
    """Serializa JSON derivado, até 64 MiB/1000000 nós, sem relaxar tipos."""
    return _dumps(value, max_nodes=MAX_OUTPUT_NODES, max_bytes=MAX_OUTPUT_BYTES)


def loads(data: bytes | str):
    """Lê UTF-8 sem BOM; recusa duplicatas, floats e limites excedidos."""
    if type(data) is bytes:
        if len(data) > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "JSON_LIMIT")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError:
            raise _error("Documento não é UTF-8 válido.") from None
    elif type(data) is str:
        source = data
        try:
            size = len(source.encode("utf-8", "strict"))
        except UnicodeError:
            raise _error("Texto contém Unicode inválido.") from None
        if size > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "JSON_LIMIT")
    else:
        raise _error("A entrada deve ser bytes ou texto.")
    if source.startswith("\ufeff"):
        raise _error("BOM não é permitido.")
    # Limitar profundidade antes do parser nativo impede estouro da pilha.
    depth = 0
    quoted = escaped = False
    for char in source:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _error("Documento excede o limite de profundidade.", "JSON_LIMIT")
        elif char in "]}":
            depth -= 1
    try:
        value = json.loads(source, parse_int=lambda token: _number(token, True), parse_float=_number,
                           parse_constant=_reject_constant, object_pairs_hook=_pairs)
    except (json.JSONDecodeError, RecursionError, OverflowError):
        raise _error("Sintaxe JSON inválida.") from None
    dumps(value)  # valida Unicode, nós, tipos e tamanho canônico
    return value
