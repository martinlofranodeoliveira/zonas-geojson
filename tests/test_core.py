"""Ouros manuais, invariantes e oráculos de intervalos independentes do motor."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from importlib.resources import files
import json
import unittest

from zonas_geojson.core import analyze
from zonas_geojson.errors import ZoneError
from zonas_geojson.jsoncodec import loads


def collection(features=()):
    return {"type": "FeatureCollection", "features": list(features)}


def feature(identifier, coordinates, geometry="Point", properties=None):
    return {"type": "Feature", "id": identifier, "properties": properties or {},
            "geometry": {"type": geometry, "coordinates": coordinates}}


def rectangle(x0=0, y0=0, x1=2, y1=2):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def zone(rings=None, identifier="z"):
    return feature(identifier, [rectangle()] if rings is None else rings, "Polygon")


def statuses(result):
    return {p["id"]: p["status"] for p in result.to_dict()["points"]}


class GoldenTests(unittest.TestCase):
    def demo(self):
        data = files("zonas_geojson").joinpath("data")
        return (loads(data.joinpath("pontos.geojson").read_bytes()),
                loads(data.joinpath("zonas.geojson").read_bytes()))

    def test_demo_manual_all_pairs_and_totals(self):
        points, zones = self.demo()
        result = analyze(points, zones).to_dict()
        self.assertEqual(result["summary"], {
            "pointCount": 13, "zoneCount": 4, "matchCount": 12,
            "insideCount": 5, "boundaryCount": 3, "ambiguousCount": 2,
            "outsideCount": 3, "pointsWithBoundary": 4,
        })
        golden = {
            "p01": ("inside", [("centro", "inside")]),
            "p02": ("outside", []),
            "p03": ("boundary", [("centro", "boundary")]),
            "p04": ("ambiguous", [("centro", "inside"), ("leste", "inside")]),
            "p05": ("ambiguous", [("centro", "boundary"), ("leste", "inside")]),
            "p06": ("inside", [("ilhas", "inside")]),
            "p07": ("inside", [("ilhas", "inside")]),
            "p08": ("inside", [("recanto", "inside")]),
            "p09": ("outside", []),
            "p10": ("boundary", [("centro", "boundary")]),
            "p11": ("inside", [("leste", "inside")]),
            "p12": ("boundary", [("ilhas", "boundary")]),
            "p13": ("outside", []),
        }
        self.assertEqual({p["id"]: (p["status"], [(m["zoneId"], m["relation"])
                                                  for m in p["matches"]])
                          for p in result["points"]}, golden)
        self.assertEqual(result["zones"], [
            {"id": "centro", "pointCount": 5, "insideCount": 2, "boundaryCount": 3},
            {"id": "ilhas", "pointCount": 3, "insideCount": 2, "boundaryCount": 1},
            {"id": "leste", "pointCount": 3, "insideCount": 3, "boundaryCount": 0},
            {"id": "recanto", "pointCount": 1, "insideCount": 1, "boundaryCount": 0},
        ])
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["algorithm"], "exact-ray-crossing")

    def test_rectangle_grid_independent_closed_interval_oracle(self):
        # Direct interval comparisons are an oracle for this rectangle only.
        points = collection(feature(f"{x}:{y}", [x, y])
                            for x in range(-2, 5) for y in range(-2, 5))
        result = statuses(analyze(points, collection([zone()])))
        for x in range(-2, 5):
            for y in range(-2, 5):
                expected = ("outside" if not (0 <= x <= 2 and 0 <= y <= 2)
                            else "boundary" if x in (0, 2) or y in (0, 2)
                            else "inside")
                self.assertEqual(result[f"{x}:{y}"], expected)

    def test_half_open_vertex_levels_and_concavity_manual(self):
        ring = [[0, 0], [6, 0], [6, 2], [2, 2], [2, 6], [0, 6], [0, 0]]
        coordinates = {
            "a": [-1, 0], "b": [7, 0], "c": [1, 2], "d": [4, 2],
            "e": [2, 4], "f": [4, 4], "g": [1, 6], "h": [7, 2],
            "i": [2, 2], "j": [6, 2], "k": [1, 4],
        }
        result = analyze(collection(feature(k, v) for k, v in coordinates.items()),
                         collection([zone([ring])]))
        self.assertEqual(statuses(result), {
            "a": "outside", "b": "outside", "c": "inside", "d": "boundary",
            "e": "boundary", "f": "outside", "g": "boundary", "h": "outside",
            "i": "boundary", "j": "boundary", "k": "inside",
        })

    def test_triangle_diagonal_exact_and_vertex_ray_levels(self):
        points = collection([feature("below", [2, 1]), feature("edge", [2, 2]),
                             feature("above", [2, 3]), feature("past", [5, 4]),
                             feature("left", [-1, 0]), feature("vertex", [4, 4])])
        triangle = [[0, 0], [4, 0], [4, 4], [0, 0]]
        self.assertEqual(statuses(analyze(points, collection([zone([triangle])]))), {
            "above": "outside", "below": "inside", "edge": "boundary",
            "past": "outside", "left": "outside", "vertex": "boundary",
        })

    def test_winding_reflection_and_translation_preserve_demo(self):
        points, zones = self.demo()
        expected = [(p["id"], p["status"], p["matches"])
                    for p in analyze(points, zones).to_dict()["points"]]
        for reflect in (False, True):
            pcopy, zcopy = deepcopy(points), deepcopy(zones)

            def transform(position):
                x, y = position
                return [(-x if reflect else x) + 20, y - 20]

            for point in pcopy["features"]:
                point["geometry"]["coordinates"] = transform(point["geometry"]["coordinates"])
            for item in zcopy["features"]:
                geometry = item["geometry"]
                polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                            else geometry["coordinates"])
                for polygon in polygons:
                    for ring in polygon:
                        ring[:] = [transform(p) for p in reversed(ring)]
            actual = [(p["id"], p["status"], p["matches"])
                      for p in analyze(pcopy, zcopy).to_dict()["points"]]
            self.assertEqual(actual, expected)

    def test_sub_float_resolution_literals_without_float_oracle(self):
        # These three decimal literals collapse to float(1); their exact order is
        # supplied by the literals, not inferred using another geometry engine.
        coordinates = {
            "before": Decimal("0.999999999999999999"),
            "on": Decimal("1.000000000000000000"),
            "after": Decimal("1.000000000000000001"),
        }
        points = collection(feature(k, [v, Decimal("0.5")]) for k, v in coordinates.items())
        with localcontext() as context:
            context.prec = 2
            result = analyze(points, collection([zone([rectangle(1, 0, 2, 1)])]))
        self.assertEqual(statuses(result), {"after": "inside", "before": "outside", "on": "boundary"})
        actual = {p["id"]: p["coordinates"][0] for p in result.to_dict()["points"]}
        self.assertEqual(actual, coordinates)
        self.assertTrue(all(type(x) is Decimal for x in actual.values()))

    def test_hole_edges_are_boundary_and_island_in_hole_is_separate(self):
        polygons = [[rectangle(0, 0, 10, 10), rectangle(2, 2, 8, 8)],
                    [rectangle(3, 3, 4, 4)]]
        zones = collection([feature("z", polygons, "MultiPolygon")])
        points = collection([feature("hole", [5, 5]), feature("edge", [2, 4]),
                             feature("island", [Decimal("3.5"), Decimal("3.5")]),
                             feature("shell", [1, 1])])
        self.assertEqual(statuses(analyze(points, zones)), {
            "edge": "boundary", "hole": "outside", "island": "inside", "shell": "inside",
        })

    def test_distinct_zones_can_touch_overlap_and_use_reserved_names(self):
        a = zone(identifier="á")
        a["properties"]["zg_status"] = "metadata"
        b = zone([rectangle(2, 0, 4, 2)], "A")
        c = zone(identifier="中")
        result = analyze(collection([feature("p", [2, 1])]), collection([a, b, c])).to_dict()
        self.assertEqual(result["points"][0]["status"], "ambiguous")
        self.assertEqual(result["points"][0]["matches"], [
            {"zoneId": "A", "relation": "boundary"},
            {"zoneId": "á", "relation": "boundary"},
            {"zoneId": "中", "relation": "boundary"},
        ])


class InvalidTopologyTests(unittest.TestCase):
    def assert_invalid(self, value):
        with self.assertRaises(ZoneError) as caught:
            analyze(collection(), collection([value]))
        self.assertEqual(caught.exception.code, "INPUT_TOPOLOGY")

    def test_rings_reject_crossing_touching_overlap_zero_area_and_closure(self):
        rings = [
            [[0, 0], [4, 4], [0, 4], [3, 0], [0, 0]],  # crossing, nonzero area
            [[0, 0], [4, 0], [4, 4], [2, 0], [0, 4], [0, 0]],  # T contact
            [[0, 0], [3, 0], [1, 0], [3, 2], [0, 2], [0, 0]],  # adjacent return
            [[0, 0], [1, 0], [2, 0], [0, 0]],
            [[0, 0], [2, 0], [2, 2], [0, 2]],
            [[0, 0], [2, 0], [2, 0], [2, 2], [0, 0]],
            [[0, 0], [2, 0], [2, 2], [2, 0], [0, 0]],
            [[0, 0], [1, 0], [0, 0]],
        ]
        for ring in rings:
            with self.subTest(ring=ring):
                self.assert_invalid(zone([ring]))

    def test_collinear_continuation_is_valid_including_closure(self):
        for ring in ([[0, 0], [1, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
                     [[1, 0], [2, 0], [2, 2], [0, 2], [0, 0], [1, 0]]):
            result = analyze(collection([feature("p", [1, 1])]), collection([zone([ring])]))
            self.assertEqual(statuses(result), {"p": "inside"})

    def test_holes_outside_touching_crossing_overlapping_and_nested(self):
        exterior = rectangle(0, 0, 10, 10)
        examples = [
            [exterior, rectangle(11, 1, 12, 2)],
            [exterior, rectangle(0, 1, 2, 2)],
            [exterior, rectangle(9, 1, 11, 2)],
            [exterior, rectangle(1, 1, 5, 5), rectangle(3, 3, 7, 7)],
            [exterior, rectangle(1, 1, 5, 5), rectangle(5, 2, 7, 3)],
            [exterior, rectangle(1, 1, 8, 8), rectangle(2, 2, 3, 3)],
            [exterior, rectangle(-1, -1, 11, 11)],
        ]
        for rings in examples:
            with self.subTest(rings=rings):
                self.assert_invalid(zone(rings))

    def test_multipolygon_touch_overlap_and_containment_rejected(self):
        for other in (rectangle(2, 0, 4, 2), rectangle(2, 2, 4, 4),
                      rectangle(1, 1, 3, 3), rectangle(0, 0, 1, 1)):
            self.assert_invalid(feature("z", [[rectangle()], [other]], "MultiPolygon"))
        self.assert_invalid(feature("z", [], "MultiPolygon"))
        self.assert_invalid(zone([]))

    def test_antimeridian_explicit_recorte(self):
        self.assert_invalid(zone([rectangle(-90, 0, 90, 1)]))
        self.assert_invalid(zone([rectangle(-179, 0, 179, 1)]))
        separated = feature("z", [[rectangle(-180, 0, -179, 1)],
                                  [rectangle(179, 0, 180, 1)]], "MultiPolygon")
        result = analyze(collection([feature("a", [-180, 0]), feature("b", [180, 0])]),
                         collection([separated]))
        self.assertEqual(statuses(result), {"a": "boundary", "b": "boundary"})


class InputAndIsolationTests(unittest.TestCase):
    def assert_refused(self, points, zones=None, code=None):
        with self.assertRaises(ZoneError) as caught:
            analyze(points, collection([zone()]) if zones is None else zones)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn("segredo", str(caught.exception))
        return caught.exception

    def test_closed_schema_at_every_level(self):
        base = collection([feature("p", [1, 1])])
        mutations = [
            lambda d: d.update(crs={}),
            lambda d: d.update(type="segredo"),
            lambda d: d.update(features={}),
            lambda d: d["features"][0].update(bbox=[]),
            lambda d: d["features"][0].update(type="segredo"),
            lambda d: d["features"][0].update(properties=None),
            lambda d: d["features"][0].update(geometry=None),
            lambda d: d["features"][0]["geometry"].update(crs={}),
            lambda d: d["features"][0]["geometry"].update(type="MultiPoint"),
            lambda d: d["features"][0]["geometry"].update(coordinates=[1, 1, 1]),
        ]
        for mutation in mutations:
            document = deepcopy(base)
            mutation(document)
            self.assert_refused(document)
        self.assert_refused([])
        self.assert_refused(base, collection([feature("z", [], "Point")]), "INPUT_SCHEMA")

    def test_unicode_ids_uniqueness_and_reserved_point_properties(self):
        for identifier in ("", " segredo", "segredo ", "segredo\n", "segredo\x7f",
                           "segredo\u2028", "segredo\u2029", "\ud800", "x" * 81):
            self.assert_refused(collection([feature(identifier, [1, 1])]))
        self.assert_refused(collection([feature("same", [1, 1]), feature("same", [2, 2])]),
                            code="INPUT_ID")
        self.assert_refused(collection(), collection([zone(identifier="same"), zone(identifier="same")]),
                            "INPUT_ID")
        for name in ("zg_point_id", "zg_status", "zg_count", "zg_boundary", "zg_matches"):
            self.assert_refused(collection([feature("p", [1, 1], properties={name: "segredo"})]),
                                code="INPUT_SCHEMA")
        result = analyze(collection([feature("中 = á", [1, 1], properties={"id": "other"})]),
                         collection([zone(identifier="中 = á")]))
        self.assertEqual(result.to_dict()["points"][0]["id"], "中 = á")

    def test_coordinates_types_ranges_and_normalized_decimal_places(self):
        for coordinates in ([True, 0], [0, False], [1.0, 0], ["segredo", 0],
                            [181, 0], [-181, 0], [0, 91], [0, -91],
                            [Decimal("0.0000000000000000001"), 0],
                            [Decimal("NaN"), 0], [Decimal("Infinity"), 0]):
            self.assert_refused(collection([feature("p", coordinates)]))
        valid = collection([feature("p", [Decimal("1.0000000000000000000"), Decimal("1E-18")])])
        self.assertEqual(statuses(analyze(valid, collection([zone()]))), {"p": "inside"})

    def test_plain_json_only_no_custom_behavior(self):
        class CustomDict(dict):
            def items(self):
                raise AssertionError("Não executar comportamento de objeto desconhecido")

        class CustomInt(int):
            pass

        self.assert_refused(CustomDict(type="FeatureCollection", features=[]))
        self.assert_refused(collection([feature("p", [CustomInt(1), 1])]))
        self.assert_refused(collection([feature("p", [1, 1], properties={"object": object()})]))

    def test_result_frozen_and_all_public_methods_return_deep_copies(self):
        points = collection([feature("z", [1, 1], properties={"nested": {"items": [1, {"a": "b"}]}}),
                             feature("a", [3, 3])])
        zones = collection([zone(identifier="á")])
        original_points, original_zones = deepcopy(points), deepcopy(zones)
        result = analyze(points, zones)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result._result = None
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result._result.items = ()
        points["features"][0]["properties"]["nested"]["items"][1]["a"] = "changed"
        points["features"].reverse()
        zones["features"][0]["geometry"]["coordinates"][0][0][0] = 100
        self.assertEqual(result.source_points(), original_points)
        self.assertEqual(result.source_zones(), original_zones)
        for method in (result.source_points, result.source_zones, result.annotated_points, result.to_dict):
            first = method()
            if "features" in first:
                first["features"][0]["properties"]["added"] = [1]
                first["features"].clear()
            else:
                first["points"][0]["matches"].append({"zoneId": "changed", "relation": "inside"})
                first["summary"]["pointCount"] = -1
            self.assertNotEqual(first, method())
        annotated = result.annotated_points()
        self.assertEqual([p["id"] for p in annotated["features"]], ["z", "a"])
        self.assertEqual([p["id"] for p in result.to_dict()["points"]], ["a", "z"])
        props = annotated["features"][0]["properties"]
        self.assertEqual(props["nested"], original_points["features"][0]["properties"]["nested"])
        self.assertEqual(props["zg_matches"], '[{"zoneId":"á","relation":"inside"}]')
        self.assertEqual(json.loads(props["zg_matches"]), result.to_dict()["points"][1]["matches"])
        self.assertEqual((props["zg_point_id"], props["zg_status"], props["zg_count"], props["zg_boundary"]),
                         ("z", "inside", 1, False))

    def test_empty_collections_and_no_floats_in_any_result(self):
        result = analyze(collection(), collection(), max_checks=1)
        data = result.to_dict()
        self.assertEqual(data["points"], [])
        self.assertEqual(data["zones"], [])
        self.assertTrue(all(value == 0 for value in data["summary"].values()))
        self.assertEqual(data["work"]["checks"], 0)
        self.assertEqual(result.annotated_points(), collection())
        points = collection([feature("p", [Decimal("1.1"), 1])])

        def inspect(value):
            self.assertIsNot(type(value), float)
            if type(value) is dict:
                for child in value.values():
                    inspect(child)
            elif type(value) is list:
                for child in value:
                    inspect(child)

        populated = analyze(points, collection([zone()]))
        for method in (populated.to_dict, populated.source_points, populated.source_zones,
                       populated.annotated_points):
            inspect(method())


class LimitTests(unittest.TestCase):
    def test_budget_unit_exact_threshold_and_bbox_count(self):
        points = collection([feature("a", [1, 1]), feature("b", [0, 0]), feature("c", [3, 3])])
        zones = collection([zone()])
        # Square: four adjacent edge-pair intersections; two opposite edge pairs
        # have disjoint boxes. Interior visits four edges, bottom-left one edge.
        work = analyze(points, zones, max_checks=9).to_dict()["work"]
        self.assertEqual(work, {"checks": 9, "limit": 9, "topologyChecks": 4,
                                "joinChecks": 5, "bboxRejectedPairs": 1})
        before = deepcopy((points, zones))
        for limit in (1, 3, 4, 8):
            with self.subTest(limit=limit), self.assertRaises(ZoneError) as caught:
                analyze(points, zones, max_checks=limit)
            self.assertEqual(caught.exception.code, "WORK_LIMIT")
            self.assertEqual((points, zones), before)

    def test_invalid_budget_values(self):
        for value in (True, False, 0, -1, 20_000_001, 1.0, Decimal("1"), "1"):
            with self.subTest(value=value), self.assertRaises(ZoneError) as caught:
                analyze(collection(), collection(), max_checks=value)
            self.assertEqual(caught.exception.code, "INPUT_LIMIT")

    def test_feature_ring_hole_and_vertex_limits(self):
        with self.assertRaises(ZoneError) as caught:
            analyze(collection(feature(str(i), [0, 0]) for i in range(10_001)), collection())
        self.assertEqual(caught.exception.code, "INPUT_LIMIT")
        with self.assertRaises(ZoneError) as caught:
            analyze(collection(), collection(zone(identifier=str(i)) for i in range(101)))
        self.assertEqual(caught.exception.code, "INPUT_LIMIT")
        with self.assertRaises(ZoneError) as caught:
            analyze(collection(), collection([zone([rectangle()] * 66)]))
        self.assertEqual(caught.exception.code, "INPUT_TOPOLOGY")
        with self.assertRaises(ZoneError) as caught:
            analyze(collection(), collection([zone([[[0, 0]] * 1_001])]))
        self.assertEqual(caught.exception.code, "INPUT_TOPOLOGY")
        # 100 * 13 rectangles = 5200 vertices, each individual component valid.
        components = [[rectangle(x, y, x + 1, y + 1)]
                      for x in range(0, 26, 2) for y in (0,)]
        with self.assertRaises(ZoneError) as caught:
            analyze(collection(), collection(feature(str(i), components, "MultiPolygon") for i in range(100)))
        self.assertEqual(caught.exception.code, "INPUT_LIMIT")

    def test_match_limit_all_or_nothing(self):
        # Boundaries terminate after one segment, keeping the real 100000-match
        # limit test small while exercising the public contract without patching.
        points = collection(feature(str(i), [0, 0]) for i in range(1_001))
        zones = collection(zone(identifier=str(i)) for i in range(100))
        with self.assertRaises(ZoneError) as caught:
            analyze(points, zones)
        self.assertEqual(caught.exception.code, "MATCH_LIMIT")
        self.assertEqual(len(points["features"]), 1_001)
        self.assertTrue(all(not p["properties"] for p in points["features"]))

    def test_match_limit_exactly_accepts_100000(self):
        points = collection(feature(str(i), [0, 0]) for i in range(1_000))
        zones = collection(zone(identifier=str(i)) for i in range(100))
        result = analyze(points, zones).to_dict()
        self.assertEqual(result["summary"]["matchCount"], 100_000)
        self.assertEqual(result["summary"]["ambiguousCount"], 1_000)
        self.assertEqual(result["summary"]["pointsWithBoundary"], 1_000)
        self.assertTrue(all(len(point["matches"]) == 100 for point in result["points"]))
        self.assertTrue(all(zone["pointCount"] == 1_000 for zone in result["zones"]))


if __name__ == "__main__":
    unittest.main()
