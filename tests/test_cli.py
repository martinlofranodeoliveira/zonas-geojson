"""CLI real e falhas de sistema de arquivos sem alteração das entradas."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from zonas_geojson.cli import main, _read_input
from zonas_geojson.errors import ZoneError
from zonas_geojson.jsoncodec import MAX_BYTES

DATA = Path(__file__).resolve().parents[1] / "src" / "zonas_geojson" / "data"


class CLITests(unittest.TestCase):
    def invoke(self, args):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            status = main(args)
        return status, output.getvalue(), error.getvalue()

    def test_example_and_actual_analyze_are_same_and_preserve_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            points, zones = folder / "p.geojson", folder / "z.geojson"
            original_points = b" \n" + (DATA / "pontos.geojson").read_bytes()
            original_zones = (DATA / "zonas.geojson").read_bytes()
            points.write_bytes(original_points)
            zones.write_bytes(original_zones)
            before = [hashlib.sha256(p.read_bytes()).digest() for p in (points, zones)]
            status, stdout, stderr = self.invoke(["analisar", str(points), str(zones), "--saida", str(folder / "analysis.zip")])
            self.assertEqual((status, stderr), (0, ""))
            self.assertIn("Relatório criado", stdout)
            self.assertEqual(before, [hashlib.sha256(p.read_bytes()).digest() for p in (points, zones)])
            with zipfile.ZipFile(folder / "analysis.zip") as archive:
                self.assertEqual(archive.read("pontos.geojson"), original_points)
                result = json.loads(archive.read("resultado.json"))
            self.assertEqual(result["summary"], {"pointCount": 13, "zoneCount": 4, "matchCount": 12, "insideCount": 5, "boundaryCount": 3, "ambiguousCount": 2, "outsideCount": 3, "pointsWithBoundary": 4})
            self.assertEqual(self.invoke(["exemplo", "--saida", str(folder / "example.zip")])[0], 0)
            with zipfile.ZipFile(folder / "example.zip") as archive:
                self.assertEqual(json.loads(archive.read("resultado.json")), result)

    def test_module_help_version_and_argument_sanitization(self):
        env = dict(os.environ, PYTHONPATH=str(DATA.parents[1]))
        completed = subprocess.run([sys.executable, "-m", "zonas_geojson", "--version"], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "zonas-geojson 1.0.0")
        help_output = subprocess.run([sys.executable, "-m", "zonas_geojson", "analisar", "--help"], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(help_output.returncode, 0)
        self.assertIn("--max-checks", help_output.stdout)
        for args in [["secreto-valor"], ["analisar", "p", "z", "--saida", "x", "--max-checks", "secreto-valor"], ["exemplo", "--saida", "x", "--secreto-valor"]]:
            status, stdout, stderr = self.invoke(args)
            self.assertEqual(status, 2)
            self.assertEqual(stdout, "")
            self.assertNotIn("secreto", stderr)
            self.assertNotIn("Traceback", stderr)

    def test_invalid_json_budget_and_existing_destination_have_no_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            bad = folder / "bad.json"
            bad.write_bytes(b'{"private-secret":NaN}')
            target = folder / "report.zip"
            status, _, stderr = self.invoke(["analisar", str(bad), str(DATA / "zonas.geojson"), "--saida", str(target)])
            self.assertEqual(status, 2)
            self.assertFalse(target.exists())
            self.assertNotIn("private-secret", stderr)
            status, _, stderr = self.invoke(["analisar", str(DATA / "pontos.geojson"), str(DATA / "zonas.geojson"), "--max-checks", "1", "--saida", str(target)])
            self.assertEqual(status, 2)
            self.assertIn("WORK_LIMIT", stderr)
            self.assertFalse(target.exists())
            target.write_bytes(b"existing")
            self.assertEqual(self.invoke(["exemplo", "--saida", str(target)])[0], 2)
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["bad.json", "report.zip"])

    def test_file_types_and_symlink_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with self.assertRaises(ZoneError):
                _read_input(str(folder))
            if hasattr(os, "mkfifo"):
                fifo = folder / "input.fifo"
                os.mkfifo(fifo)
                with self.assertRaises(ZoneError):
                    _read_input(str(fifo))
            linked = folder / "input.link"
            try:
                linked.symlink_to(DATA / "pontos.geojson")
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(ZoneError):
                _read_input(str(linked))
            victim = folder / "victim"
            victim.write_bytes(b"protected")
            target = folder / "report.zip"
            target.symlink_to(victim)
            self.assertEqual(self.invoke(["exemplo", "--saida", str(target)])[0], 2)
            self.assertTrue(target.is_symlink())
            self.assertEqual(victim.read_bytes(), b"protected")

    def test_byte_cap_is_enforced_while_reading_not_only_from_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "large"
            target.write_bytes(b" " * (MAX_BYTES + 1))
            real = target.stat()
            hidden_size = SimpleNamespace(st_mode=real.st_mode, st_dev=real.st_dev, st_ino=real.st_ino, st_size=0)
            with patch("zonas_geojson.cli.os.fstat", return_value=hidden_size):
                with self.assertRaises(ZoneError) as error:
                    _read_input(str(target))
            self.assertEqual(error.exception.code, "JSON_LIMIT")

    def test_output_parent_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing" / "report.zip"
            status, _, stderr = self.invoke(["exemplo", "--saida", str(target)])
            self.assertEqual(status, 2)
            self.assertIn("OUTPUT_PARENT", stderr)
            self.assertFalse(target.parent.exists())


if __name__ == "__main__":
    unittest.main()
