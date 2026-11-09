"""Junção cartesiana de pontos e zonas com predicados racionais exatos.

O recorte de entrada e a unidade do orçamento estão descritos em docs/API.md.
Nenhuma decisão geométrica converte coordenadas em ponto flutuante.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
import json

from .errors import ZoneError


_MAX_POINTS = 10_000
_MAX_ZONES = 100
_MAX_VERTICES = 5_000
_MAX_MATCHES = 100_000
_RESERVED = frozenset({
    "zg_point_id", "zg_status", "zg_count", "zg_boundary", "zg_matches",
})
_Point = tuple[Fraction, Fraction]
_Box = tuple[Fraction, Fraction, Fraction, Fraction]


def _fail(code: str, message: str, path: str = "") -> None:
    raise ZoneError(code, message, path)


def _schema(value: object, keys: set[str], path: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        _fail("INPUT_SCHEMA", "Objeto fora do esquema aceito.", path)
    return value


def _box(points: tuple[_Point, ...]) -> _Box:
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _boxes_overlap(a: _Box, b: _Box) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _box_contains(box: _Box, point: _Point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _orientation(a: _Point, b: _Point, c: _Point) -> Fraction:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: _Point, b: _Point, p: _Point) -> bool:
    return (_orientation(a, b, p) == 0
            and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


@dataclass(slots=True)
class _Budget:
    limit: int
    topology: int = 0
    join: int = 0
    bbox_rejected: int = 0

    def visit(self, topology: bool) -> None:
        if self.topology + self.join >= self.limit:
            _fail("WORK_LIMIT", "Orçamento de verificações excedido.")
        if topology:
            self.topology += 1
        else:
            self.join += 1


@dataclass(frozen=True, slots=True)
class _Ring:
    points: tuple[_Point, ...]
    edges: tuple[tuple[_Point, _Point], ...]
    edge_boxes: tuple[_Box, ...]
    box: _Box


@dataclass(frozen=True, slots=True)
class _Polygon:
    rings: tuple[_Ring, ...]


@dataclass(frozen=True, slots=True)
class _Zone:
    id: str
    polygons: tuple[_Polygon, ...]
    box: _Box


def _intersects(a: _Point, b: _Point, c: _Point, d: _Point,
                box_ab: _Box, box_cd: _Box, budget: _Budget) -> bool:
    if not _boxes_overlap(box_ab, box_cd):
        return False
    budget.visit(True)
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if ((o1 > 0 and o2 < 0 or o1 < 0 and o2 > 0)
            and (o3 > 0 and o4 < 0 or o3 < 0 and o4 > 0)):
        return True
    return ((o1 == 0 and _on_segment(a, b, c))
            or (o2 == 0 and _on_segment(a, b, d))
            or (o3 == 0 and _on_segment(c, d, a))
            or (o4 == 0 and _on_segment(c, d, b)))


def _locate_ring(point: _Point, ring: _Ring, budget: _Budget,
                 topology: bool) -> str:
    inside = False
    for a, b in ring.edges:
        budget.visit(topology)
        cross = _orientation(a, b, point)
        if (cross == 0 and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
                and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])):
            return "boundary"
        # The half-open y interval counts each vertex once, including concavities.
        if (a[1] > point[1]) != (b[1] > point[1]):
            if (cross > 0) == (b[1] > a[1]):
                inside = not inside
    return "inside" if inside else "outside"


def _locate_polygon(point: _Point, polygon: _Polygon, budget: _Budget,
                    topology: bool) -> str:
    outer = _locate_ring(point, polygon.rings[0], budget, topology)
    if outer != "inside":
        return outer
    for hole in polygon.rings[1:]:
        relation = _locate_ring(point, hole, budget, topology)
        if relation == "boundary":
            return "boundary"
        if relation == "inside":
            return "outside"
    return "inside"


def _rings_intersect(a: _Ring, b: _Ring, budget: _Budget) -> bool:
    for (start_a, end_a), box_a in zip(a.edges, a.edge_boxes):
        for (start_b, end_b), box_b in zip(b.edges, b.edge_boxes):
            if _intersects(start_a, end_a, start_b, end_b, box_a, box_b, budget):
                return True
    return False


def _coordinate(value: object, axis: int, path: str) -> Fraction:
    if type(value) not in (int, Decimal):
        _fail("INPUT_COORDINATE", "Coordenada deve ser um número exato.", path)
    if type(value) is Decimal:
        if not value.is_finite():
            _fail("INPUT_COORDINATE", "Coordenada deve ser finita.", path)
        parts = value.as_tuple()
        digits = parts.digits
        trailing = 0
        for digit in reversed(digits):
            if digit:
                break
            trailing += 1
        if any(digits) and parts.exponent + trailing < -18:
            _fail("INPUT_COORDINATE", "Coordenada excede dezoito casas decimais.", path)
    exact = Fraction(value)
    bound = 180 if axis == 0 else 90
    if not -bound <= exact <= bound:
        _fail("INPUT_COORDINATE", "Coordenada fora do intervalo aceito.", path)
    return exact


def _position(value: object, path: str) -> _Point:
    if type(value) is not list or len(value) != 2:
        _fail("INPUT_COORDINATE", "Posição deve conter duas coordenadas.", path)
    return (_coordinate(value[0], 0, path + "[0]"),
            _coordinate(value[1], 1, path + "[1]"))


def _ring(value: object, path: str, budget: _Budget, vertices: list[int]) -> _Ring:
    if type(value) is not list or not 4 <= len(value) <= 1_000:
        _fail("INPUT_TOPOLOGY", "Anel deve conter de quatro a mil posições.", path)
    vertices[0] += len(value)
    if vertices[0] > _MAX_VERTICES:
        _fail("INPUT_LIMIT", "Quantidade de vértices excedida.", path)
    points = tuple(_position(p, f"{path}[{i}]") for i, p in enumerate(value))
    if points[0] != points[-1]:
        _fail("INPUT_TOPOLOGY", "Anel deve estar fechado.", path)
    if len(set(points[:-1])) != len(points) - 1:
        _fail("INPUT_TOPOLOGY", "Anel contém vértices repetidos.", path)
    box = _box(points)
    if box[2] - box[0] >= 180:
        _fail("INPUT_TOPOLOGY", "Anel alcança o recorte do antimeridiano.", path)
    area_twice = sum(a[0] * b[1] - b[0] * a[1]
                     for a, b in zip(points, points[1:]))
    if area_twice == 0:
        _fail("INPUT_TOPOLOGY", "Anel deve possuir área não nula.", path)
    edges = tuple(zip(points, points[1:]))
    boxes = tuple(_box((a, b)) for a, b in edges)
    ring = _Ring(points, edges, boxes, box)
    last = len(edges) - 1
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            c, d = edges[j]
            if not _intersects(a, b, c, d, boxes[i], boxes[j], budget):
                continue
            if j == i + 1:
                # Adjacent collinear edges may continue, but may not turn back.
                invalid = (_orientation(a, b, d) == 0
                           and (b[0] - a[0]) * (d[0] - b[0])
                           + (b[1] - a[1]) * (d[1] - b[1]) < 0)
            elif i == 0 and j == last:
                invalid = (_orientation(c, d, b) == 0
                           and (d[0] - c[0]) * (b[0] - d[0])
                           + (d[1] - c[1]) * (b[1] - d[1]) < 0)
            else:
                invalid = True
            if invalid:
                _fail("INPUT_TOPOLOGY", "Anel contém encontro de bordas não permitido.", path)
    return ring


def _polygon(value: object, path: str, budget: _Budget,
             vertices: list[int]) -> _Polygon:
    if type(value) is not list or not 1 <= len(value) <= 65:
        _fail("INPUT_TOPOLOGY", "Polígono deve conter exterior e até sessenta e quatro furos.", path)
    rings = tuple(_ring(r, f"{path}[{i}]", budget, vertices)
                  for i, r in enumerate(value))
    exterior = rings[0]
    for i, hole in enumerate(rings[1:], 1):
        if (_rings_intersect(exterior, hole, budget)
                or _locate_ring(hole.points[0], exterior, budget, True) != "inside"):
            _fail("INPUT_TOPOLOGY", "Furo deve ficar estritamente dentro do exterior.", f"{path}[{i}]")
        for other in rings[1:i]:
            if (_rings_intersect(hole, other, budget)
                    or _locate_ring(hole.points[0], other, budget, True) != "outside"
                    or _locate_ring(other.points[0], hole, budget, True) != "outside"):
                _fail("INPUT_TOPOLOGY", "Furos devem ficar separados e sem aninhamento.", f"{path}[{i}]")
    return _Polygon(rings)


def _identifier(value: object, seen: set[str], path: str) -> str:
    if (type(value) is not str or not 1 <= len(value) <= 80 or value.strip() != value
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                   or ord(c) in (0x2028, 0x2029) for c in value)):
        _fail("INPUT_ID", "Identificador fora do formato aceito.", path)
    if value in seen:
        _fail("INPUT_ID", "Identificadores devem ser únicos na coleção.", path)
    seen.add(value)
    return value


def _features(document: dict, points: bool) -> list[dict]:
    prefix = "points" if points else "zones"
    _schema(document, {"type", "features"}, prefix)
    if document["type"] != "FeatureCollection" or type(document["features"]) is not list:
        _fail("INPUT_SCHEMA", "Coleção de feições inválida.", prefix)
    features = document["features"]
    if len(features) > (_MAX_POINTS if points else _MAX_ZONES):
        _fail("INPUT_LIMIT", "Quantidade de feições excedida.", prefix + ".features")
    ids: set[str] = set()
    for i, feature in enumerate(features):
        path = f"{prefix}.features[{i}]"
        _schema(feature, {"type", "id", "properties", "geometry"}, path)
        if feature["type"] != "Feature" or type(feature["properties"]) is not dict:
            _fail("INPUT_SCHEMA", "Feição ou propriedades fora do esquema aceito.", path)
        _identifier(feature["id"], ids, path + ".id")
        if points and _RESERVED.intersection(feature["properties"]):
            _fail("INPUT_SCHEMA", "Propriedades do ponto contêm nome reservado.", path + ".properties")
        geometry = _schema(feature["geometry"], {"type", "coordinates"}, path + ".geometry")
        accepted = ("Point",) if points else ("Polygon", "MultiPolygon")
        if geometry["type"] not in accepted:
            _fail("INPUT_SCHEMA", "Tipo de geometria fora do recorte aceito.", path + ".geometry.type")
    return features


def _zones(features: list[dict], budget: _Budget) -> list[_Zone]:
    zones = []
    vertices = [0]
    for i, feature in enumerate(features):
        geometry = feature["geometry"]
        path = f"zones.features[{i}].geometry.coordinates"
        coordinates = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            polygons = (_polygon(coordinates, path, budget, vertices),)
        else:
            if type(coordinates) is not list or not coordinates:
                _fail("INPUT_TOPOLOGY", "MultiPolygon deve conter ao menos um componente.", path)
            polygons = tuple(_polygon(p, f"{path}[{j}]", budget, vertices)
                             for j, p in enumerate(coordinates))
            for j, polygon in enumerate(polygons):
                for other in polygons[:j]:
                    boundary_contact = any(_rings_intersect(a, b, budget)
                                           for a in polygon.rings for b in other.rings)
                    if (boundary_contact
                            or _locate_polygon(polygon.rings[0].points[0], other, budget, True) != "outside"
                            or _locate_polygon(other.rings[0].points[0], polygon, budget, True) != "outside"):
                        _fail("INPUT_TOPOLOGY", "Componentes de MultiPolygon devem ficar separados.", path)
        bounds = tuple(p for polygon in polygons for p in polygon.rings[0].points)
        zones.append(_Zone(feature["id"], polygons, _box(bounds)))
    return sorted(zones, key=lambda zone: zone.id)


@dataclass(frozen=True, slots=True)
class _Object:
    items: tuple


@dataclass(frozen=True, slots=True)
class _Array:
    items: tuple


def _freeze(value):
    if type(value) is dict:
        return _Object(tuple((key, _freeze(child)) for key, child in value.items()))
    if type(value) is list:
        return _Array(tuple(_freeze(child) for child in value))
    return value


def _thaw(value):
    if type(value) is _Object:
        return {key: _thaw(child) for key, child in value.items}
    if type(value) is _Array:
        return [_thaw(child) for child in value.items]
    return value


@dataclass(frozen=True, slots=True)
class JoinResult:
    """Resultado imutável; cada método materializa uma cópia JSON independente."""

    _result: _Object = field(repr=False)
    _points: _Object = field(repr=False)
    _zones: _Object = field(repr=False)

    def to_dict(self) -> dict:
        return _thaw(self._result)

    def source_points(self) -> dict:
        return _thaw(self._points)

    def source_zones(self) -> dict:
        return _thaw(self._zones)

    def annotated_points(self) -> dict:
        result = self.to_dict()
        points = self.source_points()
        by_id = {point["id"]: point for point in result["points"]}
        for feature in points["features"]:
            point = by_id[feature["id"]]
            matches = point["matches"]
            feature["properties"].update({
                "zg_point_id": feature["id"],
                "zg_status": point["status"],
                "zg_count": len(matches),
                "zg_boundary": any(m["relation"] == "boundary" for m in matches),
                "zg_matches": json.dumps(matches, ensure_ascii=False, separators=(",", ":")),
            })
        return points


def analyze(points_document: dict, zones_document: dict, *,
            max_checks: int = 2_000_000) -> JoinResult:
    """Valida as fontes e junta todos os pares aceitos, ou recusa integralmente."""
    from .jsoncodec import MAX_BYTES, dumps

    if type(max_checks) is not int or not 1 <= max_checks <= 20_000_000:
        _fail("INPUT_LIMIT", "Orçamento deve ser inteiro entre um e vinte milhões.")
    documents = []
    for document in (points_document, zones_document):
        encoded = dumps(document)
        if len(encoded.encode("utf-8")) > MAX_BYTES:
            _fail("INPUT_LIMIT", "Documento canônico excede o tamanho permitido.")
        documents.append(_thaw(_freeze(document)))
    point_features = _features(documents[0], True)
    zone_features = _features(documents[1], False)
    point_positions = {
        feature["id"]: _position(feature["geometry"]["coordinates"],
                                f"points.features[{i}].geometry.coordinates")
        for i, feature in enumerate(point_features)
    }
    budget = _Budget(max_checks)
    zones = _zones(zone_features, budget)
    zone_counts = {zone.id: {"id": zone.id, "pointCount": 0,
                            "insideCount": 0, "boundaryCount": 0} for zone in zones}
    summary = {"pointCount": len(point_features), "zoneCount": len(zones),
               "matchCount": 0, "insideCount": 0, "boundaryCount": 0,
               "ambiguousCount": 0, "outsideCount": 0, "pointsWithBoundary": 0}
    points = []
    for feature in sorted(point_features, key=lambda feature: feature["id"]):
        position = point_positions[feature["id"]]
        matches = []
        for zone in zones:
            if not _box_contains(zone.box, position):
                budget.bbox_rejected += 1
                continue
            relation = "outside"
            for polygon in zone.polygons:
                relation = _locate_polygon(position, polygon, budget, False)
                if relation != "outside":
                    break
            if relation == "outside":
                continue
            if summary["matchCount"] >= _MAX_MATCHES:
                _fail("MATCH_LIMIT", "Quantidade de correspondências excedida.")
            matches.append({"zoneId": zone.id, "relation": relation})
            summary["matchCount"] += 1
            zone_counts[zone.id]["pointCount"] += 1
            zone_counts[zone.id][relation + "Count"] += 1
        status = ("outside" if not matches else "ambiguous" if len(matches) > 1
                  else matches[0]["relation"])
        summary[status + "Count"] += 1
        if any(m["relation"] == "boundary" for m in matches):
            summary["pointsWithBoundary"] += 1
        points.append({"id": feature["id"], "coordinates": feature["geometry"]["coordinates"],
                       "status": status, "matches": matches})
    result = {"version": 1, "algorithm": "exact-ray-crossing", "summary": summary,
              "points": points, "zones": list(zone_counts.values()),
              "work": {"checks": budget.topology + budget.join, "limit": budget.limit,
                       "topologyChecks": budget.topology, "joinChecks": budget.join,
                       "bboxRejectedPairs": budget.bbox_rejected}}
    return JoinResult(_freeze(result), _freeze(documents[0]), _freeze(documents[1]))
