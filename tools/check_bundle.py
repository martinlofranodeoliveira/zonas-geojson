#!/usr/bin/env python3
"""Leitor independente do formato de entrega; usa somente a biblioteca padrão.

Não importa zonas_geojson. A coerência geral não substitui um oráculo geométrico:
--demo acrescenta ouro manual; verify_external.py acrescenta GEOS e GDAL.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import xml.etree.ElementTree as ET
import zipfile


NAMES = sorted(("pontos.geojson", "zonas.geojson", "resultado.json",
                "correspondencias.csv", "pontos-anotados.geojson", "mapa.svg",
                "LEIA-ME.md", "manifesto.json"))
RESERVED = {"zg_point_id", "zg_status", "zg_count", "zg_boundary", "zg_matches"}
STATUSES = {"inside", "boundary", "outside", "ambiguous"}
DEMO_SUMMARY = {"pointCount": 13, "zoneCount": 4, "matchCount": 12,
                "insideCount": 5, "boundaryCount": 3, "ambiguousCount": 2,
                "outsideCount": 3, "pointsWithBoundary": 4}
# Transcrição do enunciado geométrico, não calculada pelo motor sob teste.
DEMO_MATCHES = {
    "p01": [("centro", "inside")], "p02": [],
    "p03": [("centro", "boundary")],
    "p04": [("centro", "inside"), ("leste", "inside")],
    "p05": [("centro", "boundary"), ("leste", "inside")],
    "p06": [("ilhas", "inside")], "p07": [("ilhas", "inside")],
    "p08": [("recanto", "inside")], "p09": [],
    "p10": [("centro", "boundary")], "p11": [("leste", "inside")],
    "p12": [("ilhas", "boundary")], "p13": [],
}
DEMO_COORDS = [(1, 1), (4, 4), (3, 4), (7, 2), (8, 2), (-4, 1),
               (-4, 5), (1, 14), (4, 14), (0, 0), (9, 0), (-3, 1), (12, 12)]
DEMO_ZONES = {
    "centro": ("Polygon", [[[0, 0], [8, 0], [8, 8], [0, 8], [0, 0]],
                           [[3, 3], [3, 5], [5, 5], [5, 3], [3, 3]]]),
    "leste": ("Polygon", [[[6, -1], [10, -1], [10, 6], [6, 6], [6, -1]]]),
    "ilhas": ("MultiPolygon", [[[[-5, 0], [-3, 0], [-3, 2], [-5, 2], [-5, 0]]],
                                [[[-5, 4], [-3, 4], [-3, 6], [-5, 6], [-5, 4]]]]),
    "recanto": ("Polygon", [[[0, 10], [6, 10], [6, 12], [2, 12], [2, 16],
                             [0, 16], [0, 10]]]),
}


class CheckError(ValueError):
    """Inconsistência sem repetir conteúdo arbitrário do arquivo."""


def require(condition, message):
    if not condition:
        raise CheckError(message)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "JSON contém chave duplicada")
        result[key] = value
    return result


def _constant(_value):
    raise CheckError("JSON contém constante não finita")


def load_json(data):
    """Preserva decimais, recusa constantes e chaves duplicadas sem usar produção."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="strict")
    require(not data.startswith("\ufeff"), "JSON contém BOM")
    return json.loads(data, parse_float=Decimal, parse_int=int,
                      object_pairs_hook=_pairs, parse_constant=_constant)


def json_equal(left, right):
    """Igualdade JSON independente: bool permanece distinto de qualquer número.

    Números exatos int/Decimal podem ter representações equivalentes.
    Python == sozinho confundiria True com 1 em uma propriedade.
    """
    if type(left) in (int, Decimal) and type(right) in (int, Decimal):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(json_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def decimal_text(value):
    require(type(value) in (int, Decimal), "Coordenada de tipo inesperado")
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def csv_text(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def match_pairs(point):
    return [(match["zoneId"], match["relation"]) for match in point["matches"]]


def check_demo(result, points, zones):
    require(json_equal(result["summary"], DEMO_SUMMARY), "Resumo diverge do ouro manual")
    require({p["id"]: match_pairs(p) for p in result["points"]} == DEMO_MATCHES,
            "Relações divergem do ouro manual")
    expected = {f"p{i:02}": list(c) for i, c in enumerate(DEMO_COORDS, 1)}
    require(json_equal({p["id"]: p["geometry"]["coordinates"] for p in points["features"]}, expected),
            "Coordenadas do exemplo divergem da especificação manual")
    actual = {z["id"]: (z["geometry"]["type"], z["geometry"]["coordinates"])
              for z in zones["features"]}
    require(all(key in actual and actual[key][0] == kind and json_equal(actual[key][1], coords)
                for key, (kind, coords) in DEMO_ZONES.items()) and set(actual) == set(DEMO_ZONES),
            "Zonas do exemplo divergem da especificação manual")


def check_result(result, points, zones):
    require(set(result) == {"version", "algorithm", "summary", "points", "zones", "work"},
            "Campos analíticos inesperados")
    require(type(result["version"]) is int and result["version"] == 1
            and result["algorithm"] == "exact-ray-crossing",
            "Versão ou algoritmo inesperado")
    source = {p["id"]: p for p in points["features"]}
    zone_ids = {z["id"] for z in zones["features"]}
    require(len(source) == len(points["features"]) and len(zone_ids) == len(zones["features"]),
            "IDs de fonte repetidos")
    require([p["id"] for p in result["points"]] == sorted(source), "Ordem/IDs de pontos incorretos")
    require([z["id"] for z in result["zones"]] == sorted(zone_ids), "Ordem/IDs de zonas incorretos")
    totals = {s: 0 for s in STATUSES}
    count = boundary_points = 0
    by_zone = {z: {"id": z, "pointCount": 0, "insideCount": 0, "boundaryCount": 0}
               for z in zone_ids}
    for point in result["points"]:
        require(set(point) == {"id", "coordinates", "status", "matches"}, "Campos de ponto incorretos")
        require(json_equal(point["coordinates"], source[point["id"]]["geometry"]["coordinates"]),
                "Coordenadas analíticas diferentes da fonte")
        pairs = match_pairs(point)
        require(all(set(m) == {"zoneId", "relation"} for m in point["matches"]),
                "Campos de correspondência incorretos")
        require(pairs == sorted(pairs) and len({z for z, _ in pairs}) == len(pairs),
                "Ordem/duplicação de correspondências incorreta")
        require(all(z in zone_ids and r in {"inside", "boundary"} for z, r in pairs),
                "Relação ou zona desconhecida")
        status = "outside" if not pairs else "ambiguous" if len(pairs) > 1 else pairs[0][1]
        require(point["status"] == status, "Status incoerente com correspondências")
        totals[status] += 1
        count += len(pairs)
        boundary_points += any(r == "boundary" for _, r in pairs)
        for zone_id, relation in pairs:
            by_zone[zone_id]["pointCount"] += 1
            by_zone[zone_id][relation + "Count"] += 1
    summary = {"pointCount": len(source), "zoneCount": len(zone_ids), "matchCount": count,
               **{key + "Count": value for key, value in totals.items()},
               "pointsWithBoundary": boundary_points}
    require(json_equal(result["summary"], summary), "Resumo não reconstrói os pontos")
    require(json_equal(result["zones"], [by_zone[z] for z in sorted(by_zone)]),
            "Contagens por zona não reconstroem os pontos")
    work = result["work"]
    require(set(work) == {"checks", "limit", "topologyChecks", "joinChecks", "bboxRejectedPairs"},
            "Campos de orçamento inesperados")
    require(all(type(v) is int and v >= 0 for v in work.values()), "Orçamento não inteiro")
    require(1 <= work["limit"] <= 20000000 and work["checks"] <= work["limit"]
            and work["checks"] == work["topologyChecks"] + work["joinChecks"],
            "Orçamento incoerente")


def check_csv(data, result):
    text = data.decode("utf-8")
    require("\r" not in text and text.endswith("\n"), "CSV deve usar UTF-8 e LF")
    expected = [["point_id", "longitude", "latitude", "status", "zone_id", "relation"]]
    for point in result["points"]:
        for zone, relation in match_pairs(point) or [("", "outside")]:
            expected.append([csv_text(point["id"]), *map(decimal_text, point["coordinates"]),
                             point["status"], csv_text(zone), relation])
    actual = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    require(actual == expected, "CSV não reconstrói todas as correspondências")
    return len(actual) - 1


def check_annotated(document, source, result):
    require(set(document) == {"type", "features"} and document["type"] == "FeatureCollection",
            "GeoJSON anotado não é a coleção esperada")
    require(len(document["features"]) == len(source["features"]), "Quantidade anotada incorreta")
    rows = {p["id"]: p for p in result["points"]}
    for annotated, original in zip(document["features"], source["features"]):
        require(set(annotated) == set(original), "Campos de feição anotada alterados")
        require(all(json_equal(annotated[k], original[k]) for k in original if k != "properties"),
                "Ordem, ID ou geometria anotada alterada")
        props = annotated["properties"]
        row = rows[original["id"]]
        require(set(props) == set(original["properties"]) | RESERVED,
                "Propriedades anotadas ausentes ou extras")
        require(all(json_equal(props[k], v) for k, v in original["properties"].items()),
                "Propriedade original alterada")
        require(props["zg_point_id"] == row["id"] and props["zg_status"] == row["status"],
                "ID/status anotado incoerente")
        require(type(props["zg_count"]) is int and props["zg_count"] == len(row["matches"]),
                "Contagem anotada incoerente")
        require(type(props["zg_boundary"]) is bool
                and props["zg_boundary"] == any(m["relation"] == "boundary" for m in row["matches"]),
                "Indicador de borda incoerente")
        require(type(props["zg_matches"]) is str and json_equal(load_json(props["zg_matches"]), row["matches"]),
                "JSON escalar de correspondências incoerente")
        require(props["zg_matches"] == json.dumps(row["matches"], ensure_ascii=False, separators=(",", ":")),
                "JSON escalar deve ser compacto e preservar Unicode")


def check_svg(data, result, zones):
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(),
            "SVG contém declaração externa")
    svg = ET.fromstring(data)
    require(svg.tag == "{http://www.w3.org/2000/svg}svg", "Raiz SVG inesperada")
    ns = {"s": "http://www.w3.org/2000/svg"}
    expected = {p["id"]: p["status"] for p in result["points"]}
    actual = {}
    label_count = 0
    for node in svg.iter():
        require(node.tag.rsplit("}", 1)[-1] not in {"script", "foreignObject", "image", "use"},
                "SVG contém conteúdo ativo ou referência externa")
        if node.tag.rsplit("}", 1)[-1] == "style":
            require("url(" not in (node.text or "").lower()
                    and "@import" not in (node.text or "").lower(), "SVG contém CSS externo")
        require(all(not key.lower().startswith("on") and "href" not in key.lower()
                    and "url(" not in value.lower() and "@import" not in value.lower()
                    for key, value in node.attrib.items()), "SVG contém referência externa")
        if node.get("class") == "point":
            displayed_id = node.get("data-point-id")
            encoding = node.get("data-point-id-encoding", "text")
            require(encoding in {"text", "json"}, "SVG declara encoding de ID desconhecido")
            point_id = load_json(displayed_id) if encoding == "json" else displayed_id
            require(type(point_id) is str, "SVG omite ID textual de ponto")
            require(point_id not in actual, "SVG repete ponto")
            actual[point_id] = node.get("data-status")
            title = node.find("s:title", ns)
            require(title is not None and displayed_id in (title.text or ""), "SVG omite ID completo")
            require(any(child.tag.rsplit("}", 1)[-1] in {"circle", "rect", "polygon", "path", "line"}
                        for child in node), "SVG omite marcador de ponto")
            label_count += len(node.findall("s:text", ns))
    require(actual == expected, "SVG não contém todos os pontos/status")
    require(label_count <= 40, "SVG excede quarenta rótulos de pontos")
    actual_rings = {}
    for node in svg.findall(".//s:path", ns):
        if node.get("fill-rule") != "evenodd":
            continue
        title = node.find("s:title", ns)
        require(title is not None, "SVG omite ID de zona")
        path = node.get("d", "")
        require(path.count("M") == path.count("Z") and path.count("M") >= 1,
                "SVG não fecha os anéis de zona")
        actual_rings.setdefault(title.text, []).append(path.count("M"))
    expected_rings = {}
    for zone in zones["features"]:
        geometry = zone["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        displayed = (json.dumps(zone["id"], ensure_ascii=True)
                     if any(ord(character) in (0xFFFE, 0xFFFF) for character in zone["id"])
                     else zone["id"])
        expected_rings.setdefault(displayed, []).extend(len(polygon) for polygon in polygons)
    expected_rings = {key: sorted(value) for key, value in expected_rings.items()}
    require({key: sorted(value) for key, value in actual_rings.items()} == expected_rings,
            "SVG não representa todos os componentes/furos com evenodd")


def check_bundle(path, *, demo=False, points_bytes=None, zones_bytes=None):
    """Valida um ZIP sem extrair nomes nem importar implementação de produção."""
    with zipfile.ZipFile(path) as bundle:
        infos = bundle.infolist()
        require([entry.filename for entry in infos] == NAMES, "ZIP deve conter oito nomes fixos em ordem")
        require(all(entry.file_size <= 64 * 1024 * 1024 for entry in infos)
                and sum(e.file_size for e in infos) <= 256 * 1024 * 1024, "ZIP excede limite de QA")
        for entry in infos:
            require(entry.date_time == (1980, 1, 1, 0, 0, 0), "Timestamp ZIP não determinístico")
            require(stat.S_IMODE(entry.external_attr >> 16) == 0o644, "Permissão ZIP inesperada")
            require(not entry.flag_bits & 1, "ZIP criptografado não suportado")
        require(bundle.testzip() is None, "CRC ZIP inválido")
        files = {name: bundle.read(name) for name in NAMES}
    manifest = load_json(files["manifesto.json"])
    require(set(manifest) == {"version", "files"} and type(manifest["version"]) is int
            and manifest["version"] == 1,
            "Manifesto de versão inesperada")
    expected = {name: {"sha256": hashlib.sha256(files[name]).hexdigest(), "size": len(files[name])}
                for name in NAMES if name != "manifesto.json"}
    require(json_equal(manifest["files"], expected), "Manifesto não confere com SHA-256/tamanhos")
    if points_bytes is not None:
        require(files["pontos.geojson"] == points_bytes, "Bytes originais de pontos alterados")
    if zones_bytes is not None:
        require(files["zonas.geojson"] == zones_bytes, "Bytes originais de zonas alterados")
    points = load_json(files["pontos.geojson"])
    zones = load_json(files["zonas.geojson"])
    result = load_json(files["resultado.json"])
    check_result(result, points, zones)
    rows = check_csv(files["correspondencias.csv"], result)
    check_annotated(load_json(files["pontos-anotados.geojson"]), points, result)
    check_svg(files["mapa.svg"], result, zones)
    require(bool(files["LEIA-ME.md"].decode("utf-8").strip()), "LEIA-ME vazio")
    if demo:
        check_demo(result, points, zones)
        require(rows == 15, "Exemplo exige quinze linhas CSV")
    return {"files": files, "result": result, "points": points, "zones": zones,
            "csvRows": rows, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Valida ZIP Zonas GeoJSON de modo independente")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--demo", action="store_true", help="Confere o ouro manual 13 pontos/4 zonas")
    parser.add_argument("--points", type=Path, help="Compara os bytes da entrada original")
    parser.add_argument("--zones", type=Path, help="Compara os bytes da entrada original")
    args = parser.parse_args(argv)
    try:
        result = check_bundle(args.bundle, demo=args.demo,
                              points_bytes=args.points.read_bytes() if args.points else None,
                              zones_bytes=args.zones.read_bytes() if args.zones else None)
    except Exception:
        # Ferramenta de QA: falha fechada; nunca imprime conteúdo/caminhos de entradas.
        print("Falha: pacote inconsistente ou ilegível.", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "sha256": result["sha256"],
                      "summary": result["result"]["summary"], "csvRows": result["csvRows"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
