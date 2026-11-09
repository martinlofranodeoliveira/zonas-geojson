"""Relatórios locais determinísticos com números de análise exatos."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from fractions import Fraction
from decimal import Decimal
from html import escape
import zipfile

from .core import analyze
from .errors import ZoneError
from .jsoncodec import dumps_output, loads, number_text

MAX_BUNDLE_BYTES = 268_435_456

REPORT_FILES = (
    "LEIA-ME.md", "correspondencias.csv", "manifesto.json", "mapa.svg",
    "pontos-anotados.geojson", "pontos.geojson", "resultado.json", "zonas.geojson",
)


def analyze_json(points: bytes | str, zones: bytes | str, *, max_checks: int = 2_000_000):
    """Analisa dois documentos UTF-8/JSON exatos usando o mesmo motor do CLI."""
    return analyze(loads(points), loads(zones), max_checks=max_checks)


def _safe_cell(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _csv(data: dict) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(("point_id", "longitude", "latitude", "status", "zone_id", "relation"))
    for point in data["points"]:
        longitude, latitude = map(number_text, point["coordinates"])
        for match in point["matches"] or [{"zoneId": "", "relation": "outside"}]:
            writer.writerow((_safe_cell(point["id"]), longitude, latitude, point["status"],
                             _safe_cell(match["zoneId"]), match["relation"]))
    return stream.getvalue().encode("utf-8")


def _xml_identifier(value: str) -> tuple[str, str]:
    # U+FFFE/U+FFFF são Unicode permitido no ID e proibido no XML 1.0.
    # Uma string JSON ASCII representa o ID integralmente, sem criar XML inválido.
    if any(ord(character) in (0xFFFE, 0xFFFF) for character in value):
        return escape(json.dumps(value, ensure_ascii=True), quote=True), "json"
    return escape(value, quote=True), "text"


def _svg(data: dict, zones: dict) -> bytes:
    """Somente a apresentação arredonda a seis casas de viewport."""
    polygons = []
    positions = [point["coordinates"] for point in data["points"]]
    for feature in zones["features"]:
        geometry = feature["geometry"]
        pieces = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        polygons.append((feature["id"], pieces))
        positions.extend(position for polygon in pieces for ring in polygon for position in ring)
    if positions:
        xs = [Fraction(p[0]) for p in positions]
        ys = [Fraction(p[1]) for p in positions]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    else:
        xmin = ymin = Fraction(0)
        xmax = ymax = Fraction(1)
    if xmin == xmax:
        xmin, xmax = xmin - Fraction(1, 2), xmax + Fraction(1, 2)
    if ymin == ymax:
        ymin, ymax = ymin - Fraction(1, 2), ymax + Fraction(1, 2)
    scale = min(Fraction(860) / (xmax - xmin), Fraction(430) / (ymax - ymin))
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2

    def xy(position):
        # A única conversão float do pacote é para desenho, após a análise.
        return float(Fraction(500) + (Fraction(position[0]) - cx) * scale), float(Fraction(320) - (Fraction(position[1]) - cy) * scale)

    def path_ring(ring):
        return " ".join(("M" if i == 0 else "L") + f"{x:.6f},{y:.6f}" for i, (x, y) in enumerate(map(xy, ring))) + " Z"

    palette = {"inside": "#12643b", "boundary": "#995500", "ambiguous": "#723e9c", "outside": "#4b5563"}
    names = {"inside": "Interior: círculo", "boundary": "Borda: quadrado", "ambiguous": "Ambíguo: losango", "outside": "Externo: cruz"}
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="720" viewBox="0 0 1000 720" role="img" aria-labelledby="title desc">',
             '<title id="title">Junção exata de pontos e zonas</title>',
             '<desc id="desc">Figura estática cartesiana em longitude e latitude. Formas e cores indicam status. Furos são vazios. Dimensões não representam distância.</desc>',
             '<rect width="1000" height="720" fill="#ffffff"/>',
             '<g font-family="sans-serif" fill="#17201c">',
             '<text x="50" y="36" font-size="23">Pontos e zonas · coordenadas cartesianas</text>',
             '<text x="50" y="61" font-size="13">Figura diagramática em graus; sem projeção, mapa-base ou escala de distância.</text>',
             '<rect x="50" y="85" width="900" height="470" rx="3" fill="#f7faf8" stroke="#c5d0c9"/>']
    for zone_id, pieces in polygons:
        for polygon in pieces:
            path = " ".join(path_ring(ring) for ring in polygon)
            lines.append(f'<path class="zone" d="{path}" fill="#b8cfbf" fill-opacity="0.43" stroke="#315a43" stroke-width="1.5" fill-rule="evenodd"><title>{_xml_identifier(zone_id)[0]}</title></path>')

    def symbol(status, x, y):
        color = palette[status]
        common = f'fill="{color}" stroke="#ffffff" stroke-width="1"'
        if status == "inside":
            return f'<circle cx="{x:.6f}" cy="{y:.6f}" r="4.5" {common}/>'
        if status == "boundary":
            return f'<rect x="{x-4.5:.6f}" y="{y-4.5:.6f}" width="9" height="9" {common}/>'
        if status == "ambiguous":
            return f'<path d="M{x:.6f},{y-6:.6f} L{x+6:.6f},{y:.6f} L{x:.6f},{y+6:.6f} L{x-6:.6f},{y:.6f} Z" {common}/>'
        return f'<path d="M{x-4:.6f},{y-4:.6f} L{x+4:.6f},{y+4:.6f} M{x-4:.6f},{y+4:.6f} L{x+4:.6f},{y-4:.6f}" fill="none" stroke="{color}" stroke-width="2.5"/>'

    for index, point in enumerate(data["points"]):
        x, y = xy(point["coordinates"])
        identifier, encoding = _xml_identifier(point["id"])
        status = point["status"]
        lines.append(f'<g class="point" data-point-id="{identifier}" data-point-id-encoding="{encoding}" data-status="{status}"><title>{identifier} ({status})</title>')
        lines.append(symbol(status, x, y))
        if index < 40:
            label = point["id"] if len(point["id"]) <= 24 else point["id"][:23] + "…"
            label = _xml_identifier(label)[0]
            label_x, anchor = (x - 8, "end") if x > 760 else (x + 8, "start")
            lines.append(f'<text x="{label_x:.6f}" y="{y-7:.6f}" text-anchor="{anchor}" font-size="11">{label}</text>')
        lines.append('</g>')
    for index, status in enumerate(palette):
        x, y = 66 + index * 235, 595
        lines.append(symbol(status, x, y))
        lines.append(f'<text x="{x+13}" y="{y+4}" font-size="13">{names[status]}</text>')
    count = min(40, len(data["points"]))
    lines.extend((f'<text x="50" y="637" font-size="13">Rótulos visíveis: {count} de {len(data["points"])} pontos (máximo 40, por ordem de ID; IDs longos abreviados).</text>',
                  '<text x="50" y="660" font-size="13">IDs completos nos títulos SVG (escape JSON para caracteres proibidos em XML) e nos arquivos JSON/CSV.</text>',
                  '<text x="50" y="683" font-size="13">Sobreposição gráfica pode ocultar símbolos; o resultado analítico conserva todos os pontos e correspondências.</text>',
                  '</g>', '</svg>', ''))
    return "\n".join(lines).encode("utf-8")


_README = """# Relatório Zonas GeoJSON

Este pacote junta pontos a zonas usando segmentos cartesianos em longitude e
latitude, com predicados racionais exatos sobre os decimais fornecidos. Não é um
geocodificador, cadastro territorial ou cálculo de distância/área real. Não usa
rede, modelos ou contas. O exemplo distribuído contém dados autorais sintéticos;
não representa endereços ou jurisdições reais.

`resultado.json` ordena pontos e zonas por ID. `inside` significa uma única zona
no interior; `boundary`, uma única zona na borda (inclusive a borda de um furo);
`ambiguous`, duas ou mais correspondências; `outside`, nenhuma. Nenhuma zona é
escolhida como vencedora. Um ponto em furo fica fora, salvo na própria borda.
`pointsWithBoundary` também inclui pontos ambíguos que tocam alguma borda.

`pontos.geojson` e `zonas.geojson` preservam os bytes das entradas. O GeoJSON
anotado preserva ordem, propriedades e valores das coordenadas, acrescentando
cinco campos `zg_`. `zg_matches` é uma string JSON compacta: decodifique-a
explicitamente para obter a lista de zonas e relações. O CSV cria uma linha por
correspondência e uma linha para cada ponto externo. IDs com =, +, - ou @ após
espaços recebem apóstrofo no CSV para reduzir interpretação como fórmula;
os IDs originais permanecem no JSON. Números permanecem exatos no JSON/CSV.
Consumidores GIS que convertem coordenadas para float podem perder precisão.

`mapa.svg` é uma figura estática sem recursos externos. A vista arredonda apenas
as posições de desenho a seis casas; a classificação não usa esse desenho.
Furos usam evenodd. Cores e formas identificam status. Até 40 IDs são rotulados;
todos constam nos títulos SVG. IDs longos são abreviados apenas nos rótulos;
U+FFFE/U+FFFF aparecem como string JSON ASCII escapada no SVG por restrição
do XML 1.0. Dimensões de viewport não são distâncias.

O orçamento `work.limit` inclui um check por interseção de segmentos e por
segmento examinado na localização de ponto, durante validação e junção. Bbox
rejeitadas não consomem checks. Ao exceder o orçamento ou 100000 matches não há
resultado parcial. Não se promete limite absoluto de tempo ou RAM.

Recorte: Point 2D, Polygon e MultiPolygon, esquema fechado, até 10000 pontos,
100 zonas e 5000 vértices; 1000 posições por anel e 64 furos por polígono.
Anéis devem ser simples e fechados; furos e componentes devem ficar separados.
Antimeridiano/span de anel >= 180 graus não é suportado. Coordenadas têm até
18 casas decimais normalizadas. JSON UTF-8 sem BOM/duplicatas: até 8 MiB
originais e canônicos, profundidade 16 e 200000 nós. Veja a documentação do
formato para os limites numéricos completos e o significado da exatidão.

`manifesto.json` lista SHA-256 e tamanho dos outros sete arquivos. O ZIP tem
nomes fixos, ordem lexicográfica, data 1980-01-01 e permissões 0644. A saída é
publicada exclusivamente: destino existente nunca é sobrescrito.
"""


def _same_json(left, right) -> bool:
    """Igualdade estrutural JSON: booleanos não são números.

    int e Decimal representam o mesmo tipo numérico JSON quando seus valores
    exatos coincidem; listas mantêm ordem e objetos mantêm chaves Unicode.
    As duas árvores já são JSON plain validado pelo codec e pelo core.
    """
    left_type, right_type = type(left), type(right)
    if left_type in (int, Decimal) and right_type in (int, Decimal):
        return left == right
    if left_type is not right_type:
        return False
    if left_type is dict:
        return left.keys() == right.keys() and all(_same_json(value, right[key]) for key, value in left.items())
    if left_type is list:
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _verified_sources(result, points_bytes: bytes, zones_bytes: bytes) -> None:
    if type(points_bytes) is not bytes or type(zones_bytes) is not bytes:
        raise ZoneError("REPORT_SOURCE", "As fontes do relatório devem ser bytes originais.")
    if not _same_json(loads(points_bytes), result.source_points()) or not _same_json(loads(zones_bytes), result.source_zones()):
        raise ZoneError("REPORT_SOURCE", "As fontes não correspondem ao resultado analisado.")


def build_report(result, points_bytes: bytes, zones_bytes: bytes) -> bytes:
    """Gera e verifica o ZIP determinístico, preservando bytes das duas fontes."""
    _verified_sources(result, points_bytes, zones_bytes)
    data = result.to_dict()
    files = {
        "pontos.geojson": points_bytes,
        "zonas.geojson": zones_bytes,
        "resultado.json": dumps_output(data).encode("utf-8"),
        "correspondencias.csv": _csv(data),
        "pontos-anotados.geojson": dumps_output(result.annotated_points()).encode("utf-8"),
        "mapa.svg": _svg(data, result.source_zones()),
        "LEIA-ME.md": _README.encode("utf-8"),
    }
    manifest = {"version": 1, "files": {name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)} for name, content in sorted(files.items())}}
    files["manifesto.json"] = dumps_output(manifest).encode("utf-8")
    if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
        raise ZoneError("REPORT_LIMIT", "Relatório excede o limite de 256 MiB descompactados.")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    result_bytes = stream.getvalue()
    _verify_report(result_bytes, files)
    return result_bytes


def _verify_report(data: bytes, expected: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if tuple(archive.namelist()) != REPORT_FILES or archive.testzip() is not None:
                raise ValueError
            for name, content in expected.items():
                received = archive.read(name)
                if len(received) != len(content) or hashlib.sha256(received).digest() != hashlib.sha256(content).digest():
                    raise ValueError
    except (ValueError, OSError, zipfile.BadZipFile):
        raise ZoneError("REPORT_INVALID", "Falha na verificação do relatório.") from None


def write_report(path: str | os.PathLike, result, points_bytes: bytes, zones_bytes: bytes) -> Path:
    """Publica exclusivamente por hardlink de staging no mesmo diretório."""
    content = build_report(result, points_bytes, zones_bytes)
    target = Path(path)
    staged = None
    try:
        if not target.parent.is_dir():
            raise ZoneError("OUTPUT_PARENT", "O diretório de saída deve existir.")
        if os.path.lexists(target):
            raise ZoneError("OUTPUT_EXISTS", "O destino já existe; não será sobrescrito.")
        descriptor, staged_name = tempfile.mkstemp(prefix=".zonas-geojson-", suffix=".tmp", dir=target.parent)
        staged = Path(staged_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644) if hasattr(os, "fchmod") else None
            os.fsync(handle.fileno())
        if staged.read_bytes() != content:
            raise ZoneError("REPORT_INVALID", "Falha na verificação do arquivo preparado.")
        try:
            os.link(staged, target)
        except FileExistsError:
            raise ZoneError("OUTPUT_EXISTS", "O destino já existe; não será sobrescrito.") from None
        except OSError:
            raise ZoneError("OUTPUT_LINK", "Não foi possível publicar por hardlink exclusivo neste sistema de arquivos.") from None
        return target
    except ZoneError:
        raise
    except (OSError, ValueError):
        raise ZoneError("OUTPUT_IO", "Não foi possível preparar o relatório no diretório de saída.") from None
    finally:
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                # O destino já pode estar publicado; falha de limpeza não invalida
                # seus bytes verificados nem permite sobrescrever outra saída.
                pass
