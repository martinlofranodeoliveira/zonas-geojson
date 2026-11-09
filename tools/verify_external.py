#!/usr/bin/env python3
"""QA independente: CLI instalada → ZIP → GEOS e GDAL/Fiona → GPKG → SQLite.

Nenhum import da implementação zonas_geojson. Dependências só neste verificador.
Execute a partir de um ambiente QA com requirements-interop.txt instalado.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import random
import re
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile

from check_bundle import CheckError, check_bundle, json_equal, load_json, match_pairs, require


class _Diagnostics:
    """Etapas autorais fixas; argumentos e dados do ensaio nunca entram no log."""
    def __init__(self):
        self.phase = "arguments"
        self.process = None

    def enter(self, phase):
        self.phase = phase
        self.process = None


def _stream_diagnostic(value):
    """Resume streams sem imprimir dados, caminhos, argumentos ou traceback."""
    raw = value.encode("utf-8", errors="replace") if isinstance(value, str) else (value or b"")
    text = raw.decode("utf-8", errors="replace")
    diagnostics = []
    for line in text.splitlines()[-12:]:
        code = re.match(r"^\[([A-Z][A-Z0-9_]{0,47})\]", line)
        exception = re.match(r"^([A-Za-z_][A-Za-z0-9_]{0,63}(?:Error|Exception)):", line)
        if code:
            diagnostics.append("[" + code.group(1) + "] <detalhe omitido>")
        elif exception:
            number = re.search(r"\[Errno ([0-9]{1,6})\]", line)
            diagnostics.append(exception.group(1) + (": errno=" + number.group(1) if number else "")
                               + " <detalhe omitido>")
        elif line == "Relatório criado." or re.fullmatch(r"zonas-geojson [0-9]+\.[0-9]+\.[0-9]+", line):
            diagnostics.append(line)
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "diagnostics": diagnostics, "rawContentOmitted": True}


def _run_process(arguments, *, diagnostics, **kwargs):
    try:
        completed = subprocess.run(arguments, capture_output=True, check=False, **kwargs)
    except subprocess.TimeoutExpired as error:
        diagnostics.process = {"stdout": _stream_diagnostic(error.stdout),
                               "stderr": _stream_diagnostic(error.stderr), "timedOut": True}
        raise
    if completed.returncode:
        diagnostics.process = {"returncode": completed.returncode,
                               "stdout": _stream_diagnostic(completed.stdout),
                               "stderr": _stream_diagnostic(completed.stderr)}
    return completed


def _failure_diagnostic(error, diagnostics):
    result = {"ok": False, "phase": diagnostics.phase, "errorClass": type(error).__name__,
              "message": "Falha na verificação externa; nenhuma aprovação emitida."}
    if isinstance(error, CheckError):
        # CheckError nasce apenas de mensagens autorais fixas nos verificadores.
        message = re.sub(r"(['\"])(?:[A-Za-z]:[\\/]|/)[^'\"]*\1", "<caminho>", str(error))
        result["detail"] = re.sub(r"(?:[A-Za-z]:[\\/]|/)[^\s'\"<>]+", "<caminho>", message)[:400]
    elif isinstance(error, OSError):
        result["errno"] = error.errno
    elif isinstance(error, subprocess.TimeoutExpired):
        result["timeoutSeconds"] = error.timeout
    if diagnostics.process is not None:
        result["process"] = diagnostics.process
    return result


def _feature(identifier, kind, coordinates, properties):
    return {"type": "Feature", "id": identifier, "properties": properties,
            "geometry": {"type": kind, "coordinates": coordinates}}


def generated_inputs():
    """Polígonos diversos e grade pseudoaleatória de quartos exatamente binários."""
    shapes = [
        ("furo", "Polygon", [[[-4, -4], [4, -4], [4, 4], [-4, 4], [-4, -4]],
                              [[-1, -1], [-1, 1], [1, 1], [1, -1], [-1, -1]]]),
        ("sobreposição", "Polygon", [[[2, -5], [6, -5], [6, 2], [2, 2], [2, -5]]]),
        ("triângulo", "Polygon", [[[8, 1], [12, 1], [10, 5], [8, 1]]]),
        ("côncavo", "Polygon", [[[-8, 3], [-2, 3], [-2, 5], [-6, 5],
                                  [-6, 9], [-8, 9], [-8, 3]]]),
        ("ilhas", "MultiPolygon", [[[[-8, -8], [-6, -8], [-6, -6], [-8, -6], [-8, -8]]],
                                    [[[0, -8], [2, -8], [2, -6], [0, -6], [0, -8]]]]),
    ]
    zones = {"type": "FeatureCollection", "features": [
        _feature(key, kind, coords, {"nome": key}) for key, kind, coords in shapes]}
    coordinates = {(-1, 0), (0, 0), (1, 0), (4, 0), (2, -4), (3, 0), (20, 20)}
    for _, kind, coords in shapes:
        for polygon in [coords] if kind == "Polygon" else coords:
            for ring in polygon:
                for a, b in zip(ring, ring[1:]):
                    coordinates.add(tuple(a))
                    coordinates.add(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    rng = random.Random(20261109)
    for _ in range(700):
        coordinates.add((rng.randrange(-40, 57) / 4, rng.randrange(-40, 41) / 4))
    points = []
    for index, coords in enumerate(sorted(coordinates)):
        identifier = ("=", "+", "-", "@")[index] + f"p{index:04}" if index < 4 else f"p{index:04}"
        if index == len(coordinates) - 1:
            identifier = 'á<&>"'
        points.append(_feature(identifier, "Point", list(coords),
                               {"nome": f'Coleta fictícia {index}: São João <&> "',
                                "sequencia": index, "ativo": index % 2 == 0}))
    # Ordem da fonte diferente da ordem analítica é uma verificação deliberada.
    points.reverse()
    return ({"type": "FeatureCollection", "features": points}, zones)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


PRECISION_POINTS = b'''{"type":"FeatureCollection","features":[
{"type":"Feature","id":"antes","properties":{"nome":"decimal anterior"},"geometry":{"type":"Point","coordinates":[0.999999999999999999,0]}},
{"type":"Feature","id":"exato","properties":{"nome":"decimal exato"},"geometry":{"type":"Point","coordinates":[1,0]}},
{"type":"Feature","id":"depois","properties":{"nome":"decimal posterior"},"geometry":{"type":"Point","coordinates":[1.000000000000000001,0]}}
]}\n'''
PRECISION_ZONES = b'''{"type":"FeatureCollection","features":[
{"type":"Feature","id":"quadrado","properties":{},"geometry":{"type":"Polygon","coordinates":[[[0,-1],[1,-1],[1,1],[0,1],[0,-1]]]}}
]}\n'''
PRECISION_GOLD = {"antes": [("quadrado", "inside")], "exato": [("quadrado", "boundary")], "depois": []}


def _plain_geometry(value):
    """Converte apenas o domínio exatamente binário usado neste ensaio externo."""
    if isinstance(value, list):
        return [_plain_geometry(item) for item in value]
    if type(value) in (int, Decimal):
        number = float(value)
        require(Fraction(number) == Fraction(value), "Ensaio GEOS recebeu coordenada não binária exata")
        return number
    return value


def check_shapely(checked):
    from shapely.geometry import Point, shape
    source = checked["points"]["features"]
    zones = checked["zones"]["features"]
    rows = {point["id"]: point for point in checked["result"]["points"]}
    polygons = [(zone["id"], shape({"type": zone["geometry"]["type"],
                                   "coordinates": _plain_geometry(zone["geometry"]["coordinates"])}))
                for zone in zones]
    require(all(polygon.is_valid for _, polygon in polygons), "GEOS recusou geometria do ensaio")
    pairs = 0
    for feature in source:
        point = Point(_plain_geometry(feature["geometry"]["coordinates"]))
        expected = []
        for identifier, polygon in polygons:
            # Predicados diferentes do ray crossing de produção, inclusive bordas de furos.
            inside, covered = polygon.contains(point), polygon.covers(point)
            require(not inside or covered, "Predicados GEOS inconsistentes")
            if covered:
                expected.append((identifier, "inside" if inside else "boundary"))
            pairs += 1
        require(match_pairs(rows[feature["id"]]) == sorted(expected), "GEOS divergiu das relações da CLI")
    return {"pairs": pairs, "pointCount": len(source), "zoneCount": len(zones)}


def _check_properties(actual, original, row):
    require(actual.get("zg_point_id") == original["id"], "GDAL perdeu o ID autoral")
    require(actual.get("zg_status") == row["status"], "GDAL alterou status")
    require(type(actual.get("zg_count")) is int and actual["zg_count"] == len(row["matches"]),
            "GDAL alterou contagem")
    require(actual.get("zg_boundary") in (False, True, 0, 1)
            and bool(actual["zg_boundary"]) == any(m["relation"] == "boundary" for m in row["matches"]),
            "GDAL alterou indicador de borda")
    require(type(actual.get("zg_matches")) is str and json_equal(load_json(actual["zg_matches"]), row["matches"]),
            "GDAL alterou JSON escalar de correspondências")
    for key, value in original["properties"].items():
        # Estes ensaios usam somente propriedades escalares suportadas pelo driver.
        require(value is None or type(value) in (str, int, bool), "Propriedade fora do ensaio GIS")
        if type(value) is bool:
            require(key in actual and type(actual[key]) in (bool, int)
                    and actual[key] in (0, 1) and bool(actual[key]) is value,
                    "GDAL alterou propriedade booleana original")
        else:
            require(key in actual and json_equal(actual[key], value), "GDAL alterou propriedade original")


def _decode_gpkg_point(blob):
    """Lê header GPKG + WKB Point2D diretamente com struct, sem GDAL/GEOS."""
    require(type(blob) is bytes and len(blob) >= 29 and blob[:3] == b"GP\x00",
            "Geometria SQLite não é GPKG versão 0")
    flags = blob[3]
    require(not flags & 0b11100000, "Flags GPKG inesperados")
    envelope = (flags >> 1) & 7
    require(envelope in (0, 1, 2, 3, 4), "Envelope GPKG desconhecido")
    offset = 8 + (0, 32, 48, 48, 64)[envelope]
    require(len(blob) == offset + 21 and blob[offset] in (0, 1), "WKB Point2D de tamanho inesperado")
    endian = "<" if blob[offset] else ">"
    require(struct.unpack_from(endian + "I", blob, offset + 1)[0] == 1, "WKB não é Point2D")
    return struct.unpack_from(endian + "dd", blob, offset + 5)


def check_gdal(checked, directory):
    import fiona
    geojson = directory / "pontos-anotados.geojson"
    geojson.write_bytes(checked["files"]["pontos-anotados.geojson"])
    gpkg = directory / "consumidor.gpkg"
    originals = {feature["id"]: feature for feature in checked["points"]["features"]}
    rows = {point["id"]: point for point in checked["result"]["points"]}

    def inspect(features):
        seen = set()
        for feature in features:
            props = dict(feature["properties"])
            identifier = props.get("zg_point_id")
            require(identifier in originals and identifier not in seen, "GDAL perdeu/duplicou pontos")
            seen.add(identifier)
            _check_properties(props, originals[identifier], rows[identifier])
            require(feature["geometry"]["type"] == "Point", "GDAL alterou tipo geométrico")
            require(tuple(feature["geometry"]["coordinates"]) == tuple(
                _plain_geometry(originals[identifier]["geometry"]["coordinates"])), "GDAL alterou coordenadas")
        require(seen == set(originals), "GDAL omitiu pontos")

    # Nenhum CRS/projeção muda durante esta cópia de features locais.
    with fiona.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=""):
        with fiona.open(geojson, driver="GeoJSON") as source:
            require(source.driver == "GeoJSON", "Driver GeoJSON não utilizado")
            schema, crs = source.schema.copy(), source.crs
            features = list(source)
            inspect(features)
        with fiona.open(gpkg, "w", driver="GPKG", layer="pontos", schema=schema, crs=crs) as target:
            target.writerecords(features)
        with fiona.open(gpkg, layer="pontos") as reopened:
            require(reopened.driver == "GPKG", "Driver GPKG não utilizado")
            inspect(reopened)

    # Consumidor separado: sqlite3 + decodificação binária própria (nenhum spatial extension).
    with sqlite3.connect(f"file:{gpkg.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity_check falhou")
        metadata = connection.execute(
            "SELECT table_name,column_name,geometry_type_name,srs_id FROM gpkg_geometry_columns").fetchall()
        require(len(metadata) == 1 and tuple(metadata[0]) == ("pontos", "geom", "POINT", 4326),
                "Metadados espaciais SQLite inesperados")
        content = connection.execute("SELECT data_type FROM gpkg_contents WHERE table_name='pontos'").fetchone()
        require(content and content[0] == "features", "GeoPackage não declara camada de feições")
        seen = set()
        for stored in connection.execute('SELECT * FROM "pontos"'):
            identifier = stored["zg_point_id"]
            require(identifier in originals and identifier not in seen, "SQLite perdeu/duplicou pontos")
            seen.add(identifier)
            _check_properties(dict(stored), originals[identifier], rows[identifier])
            require(_decode_gpkg_point(stored["geom"]) == tuple(
                _plain_geometry(originals[identifier]["geometry"]["coordinates"])),
                "SQLite/WKB divergiu das coordenadas originais")
        require(seen == set(originals), "SQLite omitiu pontos")
    return {"geojsonRead": len(originals), "gpkgReopened": len(originals),
            "sqliteRows": len(originals), "sqliteIntegrity": "ok", "sqliteWkbPoints": len(originals)}


def _environment(directory):
    profile = directory / "perfil"
    profile.mkdir()
    return {"PATH": os.defpath, "HOME": str(profile), "TMPDIR": str(directory),
            "XDG_CONFIG_HOME": str(profile), "XDG_CACHE_HOME": str(profile),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8", "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}


def _network_evidence(required):
    linux = sys.platform.startswith("linux")
    loopback_only = linux and all(name == "lo" for _, name in socket.if_nameindex())
    route_file = Path("/proc/net/route")
    no_routes = linux and route_file.is_file() and not route_file.read_text().splitlines()[1:]
    verified = bool(linux and loopback_only and no_routes)
    require(not required or verified, "É necessário um namespace Linux sem interfaces externas e sem rotas")
    return {"required": required, "linuxLoopbackOnly": bool(loopback_only),
            "noIpv4Routes": bool(no_routes), "verified": verified}


def _installed_metadata(cli, directory, env, diagnostics):
    # Interpretação isolada somente da distribuição/arquivos, sem importar o pacote.
    interpreter = cli.parent / ("python.exe" if os.name == "nt" else "python")
    code = '''import hashlib,importlib.metadata,json,sys
from pathlib import Path
package=importlib.metadata.distribution("zonas-geojson")
files={str(f):hashlib.sha256(Path(package.locate_file(f)).read_bytes()).hexdigest()
       for f in package.files if str(f).startswith("zonas_geojson/") and str(f).endswith((".py",".geojson"))}
print(json.dumps({"version":package.version,"python":sys.version.split()[0],"runtimeSha256":files},sort_keys=True))
'''
    completed = _run_process([str(interpreter), "-I", "-c", code], diagnostics=diagnostics,
                             cwd=directory, env=env, timeout=30)
    require(completed.returncode == 0, "Não foi possível verificar os arquivos da distribuição instalada")
    metadata = load_json(completed.stdout)
    require(metadata["version"] == "1.0.0" and len(metadata["runtimeSha256"]) == 9,
            "Distribuição instalada inesperada")
    return metadata


def run_verification(cli, parent=None, *, require_network_isolation=False, keep_demo=None, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else _Diagnostics()
    diagnostics.enter("dependencies.import")
    import fiona
    import shapely
    diagnostics.enter("cli.inspect")
    cli = Path(cli).resolve(strict=True)
    require(cli.is_file() and os.access(cli, os.X_OK), "CLI instalada não é executável regular")
    checkout = Path(__file__).resolve().parents[1]
    require(not cli.is_relative_to(checkout), "Instale a CLI fora do checkout para este ensaio")
    if keep_demo is not None:
        diagnostics.enter("artifacts.precheck")
        keep_demo = Path(keep_demo).resolve(strict=True)
        require(keep_demo.is_dir(), "Diretório de retenção deve existir")
    diagnostics.enter("network.inspect")
    report = {"schemaVersion": 1, "ok": True, "implementationImported": False,
              "cliOutsideCheckout": True, "temporaryInputsAndProfile": True,
              "networkIsolation": _network_evidence(require_network_isolation),
              "versions": {"python": sys.version.split()[0], "shapely": shapely.__version__,
                           "geos": shapely.geos_version_string, "fiona": fiona.__version__,
                           "gdal": fiona.__gdal_version__, "sqlite": sqlite3.sqlite_version}, "cases": {}}
    diagnostics.enter("environment.prepare")
    with tempfile.TemporaryDirectory(prefix="zonas-interop-", dir=parent) as temp:
        directory = Path(temp)
        env = _environment(directory)
        diagnostics.enter("distribution.inspect")
        report["installedDistribution"] = _installed_metadata(cli, directory, env, diagnostics)

        def invoke(*args):
            completed = _run_process([str(cli), *map(str, args)], diagnostics=diagnostics,
                                     cwd=directory, env=env, timeout=180)
            require(completed.returncode == 0, "CLI instalada recusou ensaio independente")
            return completed

        diagnostics.enter("cli.version")
        invoke("--version")
        retained = {}
        for name in ("demo", "gerado", "precisao"):
            diagnostics.enter(name + ".inputs")
            case = directory / name
            case.mkdir()
            archive = case / "resultado.zip"
            point_bytes = zone_bytes = None
            if name == "demo":
                diagnostics.enter(name + ".cli")
                invoke("exemplo", "--saida", archive)
            else:
                if name == "gerado":
                    generated_points, generated_zones = generated_inputs()
                    point_bytes, zone_bytes = _json_bytes(generated_points), _json_bytes(generated_zones)
                else:
                    point_bytes, zone_bytes = PRECISION_POINTS, PRECISION_ZONES
                (case / "pontos.geojson").write_bytes(point_bytes)
                (case / "zonas.geojson").write_bytes(zone_bytes)
                diagnostics.enter(name + ".cli")
                invoke("analisar", case / "pontos.geojson", case / "zonas.geojson", "--saida", archive)
            diagnostics.enter(name + ".bundle")
            checked = check_bundle(archive, demo=name == "demo", points_bytes=point_bytes, zones_bytes=zone_bytes)
            outcome = {"bundleSha256": checked["sha256"], "summary": checked["result"]["summary"],
                       "csvRows": checked["csvRows"], "zipEntries": 8,
                       "inputSha256": {key: hashlib.sha256(checked["files"][key]).hexdigest()
                                       for key in ("pontos.geojson", "zonas.geojson")}}
            if name == "precisao":
                diagnostics.enter(name + ".literal")
                require({p["id"]: match_pairs(p) for p in checked["result"]["points"]} == PRECISION_GOLD,
                        "CLI divergiu do ouro decimal literal")
                outcome["manualDecimalOracle"] = {"points": 3, "places": 18, "floatOracleUsed": False}
            else:
                diagnostics.enter(name + ".geos")
                outcome["shapely"] = check_shapely(checked)
                diagnostics.enter(name + ".gdal")
                outcome["gdal"] = check_gdal(checked, case)
                if name == "demo" and keep_demo is not None:
                    retained = {"demo-verificado.zip": archive.read_bytes(),
                                "demo-consumidor.gpkg": (case / "consumidor.gpkg").read_bytes()}
            report["cases"][name] = outcome
        if retained:
            diagnostics.enter("artifacts.write")
            report["retainedArtifacts"] = {}
            for name, content in retained.items():
                (keep_demo / name).write_bytes(content)
                report["retainedArtifacts"][name] = {"sha256": hashlib.sha256(content).hexdigest(),
                                                     "size": len(content)}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path, help="CLI instalada fora do checkout")
    parser.add_argument("--workdir", type=Path, help="Diretório pai existente para temporários isolados")
    parser.add_argument("--report", type=Path, help="Salva evidência JSON saneada desta execução")
    parser.add_argument("--require-network-isolation", action="store_true",
                        help="Exige Linux com somente loopback e nenhuma rota IPv4 (ex.: unshare -Urn)")
    parser.add_argument("--keep-demo", type=Path,
                        help="Retém somente ZIP e GPKG sintéticos do exemplo em diretório existente")
    args = parser.parse_args(argv)
    diagnostics = _Diagnostics()
    try:
        diagnostics.enter("report.precheck")
        if args.report:
            try:
                parent_mode = args.report.parent.stat().st_mode
            except FileNotFoundError:
                raise CheckError("O diretório pai do relatório deve existir antes do ensaio.") from None
            require(stat.S_ISDIR(parent_mode), "O diretório pai do relatório deve existir antes do ensaio.")
        diagnostics.enter("verification.start")
        report = run_verification(args.cli, args.workdir,
                                  require_network_isolation=args.require_network_isolation,
                                  keep_demo=args.keep_demo, diagnostics=diagnostics)
        diagnostics.enter("report.serialize")
        output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.report:
            diagnostics.enter("report.write")
            args.report.write_text(output, encoding="utf-8")
    except Exception as error:
        # Inclui erros de drivers binários de QA sem vazar caminhos em traceback.
        print(json.dumps(_failure_diagnostic(error, diagnostics), ensure_ascii=False, sort_keys=True),
              file=sys.stderr)
        return 2
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
