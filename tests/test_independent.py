"""Testes do consumidor independente, sem importar zonas_geojson."""
import copy
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("independent_bundle_reader", TOOLS / "check_bundle.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
external_spec = importlib.util.spec_from_file_location("independent_external_verifier", TOOLS / "verify_external.py")
external = importlib.util.module_from_spec(external_spec)
with mock.patch.dict(sys.modules, {"check_bundle": reader}):
    external_spec.loader.exec_module(external)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def fixture():
    """Ouro escrito à mão: uma coleta no quadrado, sem invocar código de produção."""
    source = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "=ponto",
              "properties": {"nome": "Coleta fictícia <&>", "ativo": True,
                             "metadados": {"itens": [False, 1, {"ok": True}]}},
              "geometry": {"type": "Point", "coordinates": [1, 1]}}]}
    zones = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "zona",
             "properties": {}, "geometry": {"type": "Polygon", "coordinates": [
                 [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}}]}
    matches = [{"zoneId": "zona", "relation": "inside"}]
    result = {"version": 1, "algorithm": "exact-ray-crossing", "summary": {
        "pointCount": 1, "zoneCount": 1, "matchCount": 1, "insideCount": 1,
        "boundaryCount": 0, "ambiguousCount": 0, "outsideCount": 0, "pointsWithBoundary": 0},
        "points": [{"id": "=ponto", "coordinates": [1, 1], "status": "inside", "matches": matches}],
        "zones": [{"id": "zona", "pointCount": 1, "insideCount": 1, "boundaryCount": 0}],
        "work": {"checks": 10, "limit": 100, "topologyChecks": 6, "joinChecks": 4, "bboxRejectedPairs": 0}}
    annotated = copy.deepcopy(source)
    annotated["features"][0]["properties"].update({"zg_point_id": "=ponto", "zg_status": "inside",
        "zg_count": 1, "zg_boundary": False,
        "zg_matches": '[{"zoneId":"zona","relation":"inside"}]'})
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" d="M0,0L2,0L2,2L0,2Z"><title>zona</title></path>
<g class="point" data-point-id="=ponto" data-status="inside"><title>=ponto (inside)</title><circle cx="1" cy="1" r="1"/></g></svg>'''
    return {"pontos.geojson": encoded(source), "zonas.geojson": encoded(zones),
            "pontos-anotados.geojson": encoded(annotated), "resultado.json": encoded(result),
            "correspondencias.csv": b"point_id,longitude,latitude,status,zone_id,relation\n'=ponto,1,1,inside,zona,inside\n",
            "mapa.svg": svg, "LEIA-ME.md": "Dados fictícios; coordenadas cartesianas.\n".encode("utf-8")}


def archive(files, *, manifest_override=None, reverse=False, mode=0o644, timestamp=(1980, 1, 1, 0, 0, 0)):
    files = dict(files)
    manifest = {"version": 1, "files": {name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                                        for name, value in files.items()}}
    files["manifesto.json"] = encoded(manifest_override or manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(files, reverse=reverse):
            entry = zipfile.ZipInfo(name, date_time=timestamp)
            entry.create_system = 3
            entry.external_attr = mode << 16
            bundle.writestr(entry, files[name])
    return output.getvalue()


class IndependentReaderTests(unittest.TestCase):
    def check(self, data, **kwargs):
        with tempfile.TemporaryDirectory(prefix="zonas-reader-") as temporary:
            path = Path(temporary) / "exemplo.zip"
            path.write_bytes(data)
            return reader.check_bundle(path, **kwargs)

    def test_handwritten_archive_without_production(self):
        files = fixture()
        checked = self.check(archive(files), points_bytes=files["pontos.geojson"],
                             zones_bytes=files["zonas.geojson"])
        self.assertEqual(checked["result"]["summary"]["insideCount"], 1)
        self.assertEqual(checked["csvRows"], 1)

    def test_original_bytes_are_checked_independently_of_semantics(self):
        with self.assertRaises(reader.CheckError):
            self.check(archive(fixture()), points_bytes=b"{}\n")

    def test_manifest_and_zip_metadata_are_required(self):
        for kwargs in ({"reverse": True}, {"mode": 0o777}, {"timestamp": (2026, 1, 1, 0, 0, 0)},
                       {"manifest_override": {"version": 1, "files": {}}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(reader.CheckError):
                self.check(archive(fixture(), **kwargs))

    def test_semantic_corruption_is_detected_even_after_rehash(self):
        mutations = {
            "csv-count": ("correspondencias.csv", lambda b: b + b"'=ponto,1,1,inside,zona,inside\n"),
            "csv-formula": ("correspondencias.csv", lambda b: b.replace(b"'=ponto", b"=ponto")),
            "annotation-name": ("pontos-anotados.geojson", lambda b: b.replace(b"Coleta", b"Outra")),
            "annotation-bool": ("pontos-anotados.geojson", lambda b: b.replace(b'"ativo": true', b'"ativo": 1')),
            "annotation-nested-bool": ("pontos-anotados.geojson", lambda b: b.replace(b'"ok": true', b'"ok": 1')),
            "annotation-id": ("pontos-anotados.geojson", lambda b: b.replace(b'"zg_point_id": "=ponto"', b'"zg_point_id": "outro"')),
            "summary": ("resultado.json", lambda b: b.replace(b'"matchCount": 1', b'"matchCount": 2')),
            "coords": ("resultado.json", lambda b: b.replace(b'"coordinates": [\n        1,', b'"coordinates": [\n        9,')),
            "coords-bool": ("resultado.json", lambda b: b.replace(b'"coordinates": [\n        1,', b'"coordinates": [\n        true,')),
            "svg-point": ("mapa.svg", lambda b: b.replace(b'data-status="inside"', b'data-status="outside"')),
            "svg-external": ("mapa.svg", lambda b: b.replace(b"</svg>", b'<image href="https://invalid.example/a.png"/></svg>')),
        }
        for label, (name, mutate) in mutations.items():
            with self.subTest(label=label):
                files = fixture()
                changed = mutate(files[name])
                self.assertNotEqual(changed, files[name], "Mutação deve exercer um defeito real")
                files[name] = changed
                with self.assertRaises(reader.CheckError):
                    self.check(archive(files))

    def test_exact_json_and_canonical_csv_numbers(self):
        parsed = reader.load_json(b'[0.999999999999999999,1,1.000000000000000001]')
        self.assertEqual(parsed, [Decimal("0.999999999999999999"), 1, Decimal("1.000000000000000001")])
        self.assertLess(parsed[0], parsed[1])
        self.assertGreater(parsed[2], parsed[1])
        self.assertEqual([reader.decimal_text(p) for p in parsed],
                         ["0.999999999999999999", "1", "1.000000000000000001"])
        self.assertEqual(reader.decimal_text(Decimal("-0.000")), "0")
        self.assertEqual(reader.decimal_text(Decimal("1E+18")), "1000000000000000000")

    def test_json_equality_preserves_boolean_types_recursively(self):
        self.assertFalse(reader.json_equal(True, 1))
        self.assertFalse(reader.json_equal(False, 0))
        self.assertFalse(reader.json_equal({"a": [True]}, {"a": [Decimal("1.0")]}))
        self.assertTrue(reader.json_equal({"a": [1, None]}, {"a": [Decimal("1.000"), None]}))

    def test_duplicate_keys_and_nonfinite_constants_are_refused(self):
        for value in ('{"a":1,"a":2}', '[NaN]', '[Infinity]', '\ufeff{}'):
            with self.subTest(value=value), self.assertRaises(reader.CheckError):
                reader.load_json(value)

    def test_demo_gold_is_literal_and_checks_every_point(self):
        points = {"features": [{"id": f"p{i:02}", "geometry": {"coordinates": list(coords)}}
                              for i, coords in enumerate(reader.DEMO_COORDS, 1)]}
        zones = {"features": [{"id": key, "geometry": {"type": kind, "coordinates": coords}}
                             for key, (kind, coords) in reader.DEMO_ZONES.items()]}
        result = {"summary": reader.DEMO_SUMMARY, "points": [
            {"id": key, "matches": [{"zoneId": z, "relation": r} for z, r in pairs]}
            for key, pairs in reader.DEMO_MATCHES.items()]}
        reader.check_demo(result, points, zones)
        result = copy.deepcopy(result)
        result["points"][1]["matches"] = [{"zoneId": "centro", "relation": "inside"}]
        with self.assertRaises(reader.CheckError):
            reader.check_demo(result, points, zones)

    def test_damaged_compressed_payload_is_refused(self):
        # Altera bytes do primeiro payload mantendo o CRC original no diretório ZIP.
        raw = bytearray(archive(fixture()))
        name_size, extra_size = int.from_bytes(raw[26:28], "little"), int.from_bytes(raw[28:30], "little")
        offset = 30 + name_size + extra_size
        raw[offset] ^= 0x01
        with self.assertRaises((reader.CheckError, zipfile.BadZipFile)):
            self.check(bytes(raw))


class ExternalDiagnosticsTests(unittest.TestCase):
    """Exercita falhas com mocks; não importa produção nem executa GEOS/GDAL."""
    def invoke(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = external.main(arguments)
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        return json.loads(stderr.getvalue()), stderr.getvalue()

    def test_missing_report_parent_fails_before_verification(self):
        with tempfile.TemporaryDirectory(prefix="zonas-private-marker-") as temporary:
            report = Path(temporary) / "private-report-parent" / "evidencia.json"
            with mock.patch.object(external, "run_verification") as verify:
                result, raw = self.invoke(["--cli", "private-cli-input", "--report", str(report)])
            verify.assert_not_called()
            self.assertFalse(report.parent.exists())
        self.assertEqual(result["phase"], "report.precheck")
        self.assertEqual(result["errorClass"], "CheckError")
        self.assertIn("diretório pai", result["detail"])
        for private in ("private-cli-input", "private-report-parent", "zonas-private-marker-", temporary):
            self.assertNotIn(private, raw)

    def test_errno_and_phase_survive_without_private_oserror_text(self):
        def fail(*args, diagnostics, **kwargs):
            diagnostics.enter("demo.gdal")
            raise PermissionError(errno.EACCES, "PRIVATE_CREDENTIAL_VALUE", "/home/private-profile/private-file")
        with mock.patch.object(external, "run_verification", side_effect=fail):
            result, raw = self.invoke(["--cli", "private-cli-input"])
        self.assertEqual((result["phase"], result["errorClass"], result["errno"]),
                         ("demo.gdal", "PermissionError", errno.EACCES))
        for private in ("PRIVATE_CREDENTIAL_VALUE", "/home/", "private-profile", "private-cli-input"):
            self.assertNotIn(private, raw)

    def test_report_write_error_identifies_write_phase(self):
        with tempfile.TemporaryDirectory(prefix="zonas-private-marker-") as temporary:
            report = Path(temporary) / "private-output.json"
            error = PermissionError(errno.EACCES, "PRIVATE_WRITE_SECRET", str(report))
            with mock.patch.object(external, "run_verification", return_value={"ok": True}), \
                 mock.patch.object(Path, "write_text", side_effect=error):
                result, raw = self.invoke(["--cli", "private-cli-input", "--report", str(report)])
        self.assertEqual(result["phase"], "report.write")
        self.assertEqual(result["errno"], errno.EACCES)
        self.assertNotIn("PRIVATE_WRITE_SECRET", raw)
        self.assertNotIn(temporary, raw)

    def test_failed_process_omits_data_arguments_paths_and_traceback(self):
        stdout = b'{"nome":"PRIVATE_PAYLOAD_VALUE"}'
        stderr = (b'Traceback (most recent call last):\n  File "/home/private-profile/secret.py", line 2\n'
                  b'PermissionError: [Errno 13] Permission denied: "/home/private profile/secret"\n'
                  b'[CLI_IO] PRIVATE_STREAM_VALUE\n')
        completed = subprocess.CompletedProcess(["/home/private/cli", "PRIVATE_ARGUMENT"], 2, stdout, stderr)
        def fail(*args, diagnostics, **kwargs):
            diagnostics.enter("gerado.cli")
            result = external._run_process(["PRIVATE_ARGUMENT"], diagnostics=diagnostics, timeout=1)
            reader.require(result.returncode == 0, "CLI instalada recusou ensaio independente")
        with mock.patch.object(external, "run_verification", side_effect=fail), \
             mock.patch.object(external.subprocess, "run", return_value=completed):
            result, raw = self.invoke(["--cli", "private-cli-input"])
        self.assertEqual(result["phase"], "gerado.cli")
        self.assertEqual(result["process"]["returncode"], 2)
        self.assertEqual(result["detail"], "CLI instalada recusou ensaio independente")
        self.assertEqual(result["process"]["stdout"]["bytes"], len(stdout))
        self.assertIn("PermissionError: errno=13 <detalhe omitido>",
                      result["process"]["stderr"]["diagnostics"])
        self.assertIn("[CLI_IO] <detalhe omitido>", result["process"]["stderr"]["diagnostics"])
        for private in ("PRIVATE_PAYLOAD_VALUE", "PRIVATE_STREAM_VALUE", "PRIVATE_ARGUMENT", "/home/", "Traceback"):
            self.assertNotIn(private, raw)

    def test_timeout_identifies_phase_without_repeating_command(self):
        def fail(*args, diagnostics, **kwargs):
            diagnostics.enter("distribution.inspect")
            external._run_process(["PRIVATE_COMMAND"], diagnostics=diagnostics, timeout=3)
        timeout = subprocess.TimeoutExpired(["/home/private/cli"], 3,
                                            output=b"PRIVATE_TIMEOUT_VALUE", stderr=b"PRIVATE_STDERR")
        with mock.patch.object(external, "run_verification", side_effect=fail), \
             mock.patch.object(external.subprocess, "run", side_effect=timeout):
            result, raw = self.invoke(["--cli", "private-cli-input"])
        self.assertEqual(result["phase"], "distribution.inspect")
        self.assertEqual(result["errorClass"], "TimeoutExpired")
        self.assertEqual(result["timeoutSeconds"], 3)
        self.assertTrue(result["process"]["timedOut"])
        for private in ("PRIVATE_COMMAND", "PRIVATE_TIMEOUT_VALUE", "PRIVATE_STDERR", "/home/"):
            self.assertNotIn(private, raw)


if __name__ == "__main__":
    unittest.main()
