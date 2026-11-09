"""Contratos de serialização e artefatos, verificados por leitores independentes."""
import csv
from decimal import Decimal, localcontext, getcontext
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from zonas_geojson.errors import ZoneError
from zonas_geojson.jsoncodec import loads, dumps, dumps_output, MAX_BYTES
from zonas_geojson.reports import analyze_json, build_report, write_report, REPORT_FILES


def sources():
    points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": '=SUM(1,2)<&"', "properties": {"nome": "São Paulo", "n": Decimal("1.000000000000000001")}, "geometry": {"type": "Point", "coordinates": [Decimal("1.000000000000000001"), 1]}},
        {"type": "Feature", "id": "externo", "properties": {}, "geometry": {"type": "Point", "coordinates": [9, 9]}}]}
    zones = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "@zona", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]], [[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]]]}}]}
    return (" \n" + dumps(points)).encode(), dumps(zones).encode()


class CodecTests(unittest.TestCase):
    def test_exact_roundtrip_ignores_decimal_context(self):
        before = getcontext().copy()
        value = loads(b'{"x":1.000000000000000001,"small":0.000000000000000000000000000000000000001e-24,"big":9999999999999999999999999999999999999999e24}')
        with localcontext() as context:
            context.prec = 1
            rendered = dumps(value)
            self.assertEqual(loads(rendered), value)
            self.assertIn("1.000000000000000001", rendered)
            self.assertEqual(context.prec, 1)
        self.assertEqual(getcontext().prec, before.prec)
        self.assertEqual(getcontext().rounding, before.rounding)

    def test_strict_invalid_documents_and_limits(self):
        invalid = [b'\xef\xbb\xbf{}', b'\xff', '{"a":1,"a":2}', '{"a":1,"\\u0061":2}', 'NaN', 'Infinity', '-Infinity', '1e25', '1e-25', '1' * 41, '"\\ud800"', '[' * 17 + '0' + ']' * 17, '{"a":01}', 'true trailing']
        for value in invalid:
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ZoneError):
                loads(value)
        self.assertEqual(loads('[' * 16 + '0' + ']' * 16), [[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]])
        with self.assertRaises(ZoneError):
            loads(b' ' * (MAX_BYTES + 1))
        # Input bytes fit, but pretty canonical form exceeds the input cap.
        with self.assertRaises(ZoneError):
            loads('[' + ','.join('"' + 'x' * 100 + '"' for _ in range(81000)) + ']')

    def test_plain_types_only_and_no_object_behavior(self):
        class Evil(dict):
            def items(self):
                raise AssertionError("must not run")
        class Text(str):
            pass
        for value in [1.5, float('nan'), Evil(), {Text('a'): 1}, {1: 'a'}, (1, 2), Decimal('NaN'), Decimal('Infinity'), 10 ** 10000]:
            with self.subTest(kind=type(value)), self.assertRaises(ZoneError):
                dumps(value)
        cycle = []
        cycle.append(cycle)
        with self.assertRaises(ZoneError):
            dumps(cycle)

    def test_output_has_separate_limits_for_many_matches(self):
        records = {"matches": [{"zoneId": "z" * 80, "relation": "inside"} for _ in range(80000)]}
        with self.assertRaises(ZoneError):
            dumps(records)
        encoded = dumps_output(records)
        self.assertGreater(len(encoded.encode()), MAX_BYTES)
        self.assertEqual(len(json.loads(encoded)["matches"]), 80000)
        with self.assertRaises(ZoneError):
            dumps_output({"number": 1.5})


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.points, self.zones = sources()
        self.result = analyze_json(self.points, self.zones)

    def test_zip_manifest_sources_csv_xml_and_determinism(self):
        content = build_report(self.result, self.points, self.zones)
        self.assertEqual(content, build_report(self.result, self.points, self.zones))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(tuple(archive.namelist()), REPORT_FILES)
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("pontos.geojson"), self.points)
            self.assertEqual(archive.read("zonas.geojson"), self.zones)
            manifest = json.loads(archive.read("manifesto.json"))
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(set(manifest["files"]), set(REPORT_FILES) - {"manifesto.json"})
            for name, entry in manifest["files"].items():
                payload = archive.read(name)
                self.assertEqual(entry, {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16 & 0o777, 0o644)
            analytical = json.loads(archive.read("resultado.json"), parse_float=Decimal)
            self.assertEqual(analytical["points"][0]["coordinates"][0], Decimal("1.000000000000000001"))
            rows = list(csv.DictReader(io.StringIO(archive.read("correspondencias.csv").decode())))
            self.assertEqual(rows[0]["point_id"], '\'=SUM(1,2)<&"')
            self.assertEqual(rows[0]["zone_id"], "'@zona")
            self.assertEqual(rows[0]["longitude"], "1.000000000000000001")
            self.assertEqual(rows[1]["relation"], "outside")
            self.assertNotIn(b'\r\n', archive.read("correspondencias.csv"))
            annotated = json.loads(archive.read("pontos-anotados.geojson"), parse_float=Decimal)
            props = annotated["features"][0]["properties"]
            self.assertEqual(props["nome"], "São Paulo")
            self.assertEqual(props["n"], Decimal("1.000000000000000001"))
            self.assertEqual(json.loads(props["zg_matches"]), [{"zoneId": "@zona", "relation": "inside"}])
            svg = ET.fromstring(archive.read("mapa.svg"))
            ns = {"s": "http://www.w3.org/2000/svg"}
            markers = [node for node in svg.findall('.//s:g', ns) if node.get("class") == "point"]
            self.assertEqual([node.attrib["data-point-id"] for node in markers], ['=SUM(1,2)<&"', "externo"])
            self.assertTrue(svg.findall('.//s:path[@fill-rule="evenodd"]', ns))
            self.assertNotIn(b'https:', archive.read("mapa.svg"))
            self.assertNotIn(b'<!ENTITY', archive.read("mapa.svg"))

    def test_all_points_but_only_forty_labels(self):
        document = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": f"p{n:02}", "properties": {}, "geometry": {"type": "Point", "coordinates": [n, 0]}} for n in range(45)]}
        points = dumps(document).encode()
        result = analyze_json(points, self.zones)
        with zipfile.ZipFile(io.BytesIO(build_report(result, points, self.zones))) as archive:
            svg = ET.fromstring(archive.read("mapa.svg"))
        groups = [node for node in svg.iter() if node.get("class") == "point"]
        self.assertEqual(len(groups), 45)
        self.assertEqual(sum(len([x for x in node if x.tag.endswith('text')]) for node in groups), 40)
        self.assertTrue(all(any(x.tag.endswith('title') for x in node) for node in groups))

    def test_xml_for_unicode_noncharacters_preserves_id_reversibly(self):
        identifier = "nome" + chr(0xFFFE) + chr(0xFFFF) + "\\literal"
        document = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": identifier, "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]}
        points = dumps(document).encode()
        result = analyze_json(points, self.zones)
        with zipfile.ZipFile(io.BytesIO(build_report(result, points, self.zones))) as archive:
            svg = ET.fromstring(archive.read("mapa.svg"))
            self.assertEqual(json.loads(archive.read("resultado.json"))["points"][0]["id"], identifier)
        group = next(node for node in svg.iter() if node.get("class") == "point")
        self.assertEqual(group.get("data-point-id-encoding"), "json")
        self.assertEqual(json.loads(group.get("data-point-id")), identifier)

    def test_mismatched_sources_rejected_before_output(self):
        with self.assertRaises(ZoneError):
            build_report(self.result, b'{"type":"FeatureCollection","features":[]}', self.zones)

    def test_source_binding_distinguishes_nested_booleans_from_numbers(self):
        points_document = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "point", "properties": {"nested": [{"active": True, "disabled": False}]}, "geometry": {"type": "Point", "coordinates": [1, 0]}}]}
        zones_document = loads(self.zones)
        zones_document["features"][0]["properties"] = {"nested": [{"active": True}]}
        points, zones = dumps(points_document).encode(), dumps(zones_document).encode()
        result = analyze_json(points, zones)
        variants = []
        for field, replacement in [("active", 1), ("disabled", 0)]:
            changed = loads(points)
            changed["features"][0]["properties"]["nested"][0][field] = replacement
            variants.append((dumps(changed).encode(), zones))
        changed_zones = loads(zones)
        changed_zones["features"][0]["properties"]["nested"][0]["active"] = 1
        variants.append((points, dumps(changed_zones).encode()))
        with tempfile.TemporaryDirectory() as directory:
            for index, (changed_points, changed_zones) in enumerate(variants):
                target = Path(directory) / f"refused-{index}.zip"
                with self.subTest(index=index), self.assertRaises(ZoneError) as error:
                    write_report(target, result, changed_points, changed_zones)
                self.assertEqual(error.exception.code, "REPORT_SOURCE")
                self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_source_binding_rejects_boolean_coordinates_equal_to_integers(self):
        original = b'{"type":"FeatureCollection","features":[{"type":"Feature","id":"point","properties":{},"geometry":{"type":"Point","coordinates":[1,0]}}]}'
        result = analyze_json(original, self.zones)
        changed = original.replace(b'[1,0]', b'[true,false]')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "refused.zip"
            with self.assertRaises(ZoneError) as error:
                write_report(target, result, changed, self.zones)
            self.assertEqual(error.exception.code, "REPORT_SOURCE")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_source_binding_accepts_exact_int_decimal_numeric_equivalence(self):
        decimals = b'{"type":"FeatureCollection","features":[{"type":"Feature","id":"point","properties":{"nested":[1.0,{"score":0.0,"active":true}]},"geometry":{"type":"Point","coordinates":[1.0,0.0]}}]}'
        integers = b'{"type":"FeatureCollection","features":[{"type":"Feature","id":"point","properties":{"nested":[1,{"score":0,"active":true}]},"geometry":{"type":"Point","coordinates":[1,0]}}]}'
        result = analyze_json(decimals, self.zones)
        archive_bytes = build_report(result, integers, self.zones)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            self.assertEqual(archive.read("pontos.geojson"), integers)
            annotated = json.loads(archive.read("pontos-anotados.geojson"))
        self.assertIs(annotated["features"][0]["properties"]["nested"][1]["active"], True)
        self.assertEqual(annotated["features"][0]["geometry"]["coordinates"], [1, 0])

    def test_atomic_publication_refusal_and_fault_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.zip"
            write_report(target, self.result, self.points, self.zones)
            original = target.read_bytes()
            with self.assertRaises(ZoneError):
                write_report(target, self.result, self.points, self.zones)
            self.assertEqual(target.read_bytes(), original)
            with patch("zonas_geojson.reports.os.link", side_effect=OSError("private path")):
                with self.assertRaises(ZoneError) as error:
                    write_report(Path(directory) / "failed.zip", self.result, self.points, self.zones)
            self.assertEqual(error.exception.code, "OUTPUT_LINK")
            self.assertNotIn("private", str(error.exception))
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["report.zip"])
            with self.assertRaises(ZoneError):
                write_report(Path(directory) / "missing" / "out.zip", self.result, self.points, self.zones)

    def test_no_overwrite_when_destination_appears_during_commit(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.zip"
            real_link = os.link
            def raced(source, destination):
                target.write_bytes(b"other writer")
                return real_link(source, destination)
            with patch("zonas_geojson.reports.os.link", side_effect=raced), self.assertRaises(ZoneError):
                write_report(target, self.result, self.points, self.zones)
            self.assertEqual(target.read_bytes(), b"other writer")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_crash_before_commit_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.zip"
            with patch("zonas_geojson.reports.os.fsync", side_effect=OSError("private")), self.assertRaises(ZoneError):
                write_report(target, self.result, self.points, self.zones)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
