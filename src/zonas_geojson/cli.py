"""Interface local, sem rede e sem sobrescrita de saídas."""
from __future__ import annotations

import argparse
from importlib import resources
import os
import stat
import sys

from . import __version__
from .errors import ZoneError
from .jsoncodec import MAX_BYTES
from .reports import analyze_json, write_report


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse inclui valores fornecidos pelo usuário em vários erros.
        raise ZoneError("CLI_ARGUMENT", "Argumentos inválidos; consulte --help.")


def _checks(value: str) -> int:
    try:
        if len(value) > 8 or not value.isascii() or not value.isdecimal():
            raise ValueError
        number = int(value)
        if not 1 <= number <= 20_000_000:
            raise ValueError
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("Orçamento deve ser inteiro entre 1 e 20000000.") from None


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="zonas-geojson", description="Junta pontos a zonas por predicados exatos; processamento local e cartesiano em graus.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    analyze_command = commands.add_parser("analisar", help="Analisar duas FeatureCollections GeoJSON.")
    analyze_command.add_argument("pontos", help="Arquivo regular UTF-8 de pontos (sem symlink).")
    analyze_command.add_argument("zonas", help="Arquivo regular UTF-8 de zonas (sem symlink).")
    analyze_command.add_argument("--saida", required=True, help="Novo ZIP; o diretório deve existir.")
    analyze_command.add_argument("--max-checks", type=_checks, default=2_000_000, help="Orçamento lógico de 1 a 20000000 (padrão: 2000000).")
    example = commands.add_parser("exemplo", help="Gerar o exemplo autoral de dados fictícios.")
    example.add_argument("--saida", required=True, help="Novo ZIP; nunca sobrescreve destino existente.")
    return parser


def _read_input(path: str) -> bytes:
    descriptor = None
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise ZoneError("INPUT_FILE", "A entrada deve ser arquivo regular explícito, sem symlink.")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        after = os.lstat(path)
        if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise ZoneError("INPUT_FILE", "A entrada foi alterada ou não é arquivo regular explícito.")
        if opened.st_size > MAX_BYTES:
            raise ZoneError("JSON_LIMIT", "Documento excede o limite de bytes.")
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise ZoneError("JSON_LIMIT", "Documento excede o limite de bytes.")
            chunks.append(chunk)
        return b"".join(chunks)
    except ZoneError:
        raise
    except (OSError, ValueError):
        raise ZoneError("INPUT_IO", "Não foi possível ler a entrada como arquivo regular.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    """Retorna 0 no sucesso, 2 em recusa; erros não repetem dados de entrada."""
    try:
        args = _parser().parse_args(argv)
        if args.command == "exemplo":
            package = resources.files("zonas_geojson").joinpath("data")
            points = package.joinpath("pontos.geojson").read_bytes()
            zones = package.joinpath("zonas.geojson").read_bytes()
            limit = 2_000_000
        else:
            points, zones = _read_input(args.pontos), _read_input(args.zonas)
            limit = args.max_checks
        result = analyze_json(points, zones, max_checks=limit)
        write_report(args.saida, result, points, zones)
        print("Relatório criado.")
        return 0
    except ZoneError as error:
        print(f"[{error.code}] {error.message}", file=sys.stderr)
        return 2
    except Exception:
        print("[CLI_IO] Não foi possível concluir a operação local.", file=sys.stderr)
        return 2
