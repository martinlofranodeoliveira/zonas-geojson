"""Casos adversariais do contrato público de distribuição."""
import base64
import csv
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_distribution.py'
spec = importlib.util.spec_from_file_location('distribution_checker', SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'src/zonas_geojson').mkdir(parents=True)
        (self.root / 'src/zonas_geojson/__init__.py').write_bytes(b'__version__ = "1.0.0"\n')
        (self.root / 'LICENSE').write_bytes(b'MIT synthetic fixture\n')

    def wheel(self, change=None):
        d = checker.DISTINFO
        files = {
            'zonas_geojson/__init__.py': b'__version__ = "1.0.0"\n',
            d + '/licenses/LICENSE': b'MIT synthetic fixture\n',
            d + '/METADATA': b'Metadata-Version: 2.4\nName: zonas-geojson\nVersion: 1.0.0\nRequires-Python: >=3.11\nLicense-Expression: MIT\n\n',
            d + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
            d + '/entry_points.txt': b'[console_scripts]\nzonas-geojson = zonas_geojson.cli:main\n',
            d + '/top_level.txt': b'zonas_geojson\n',
        }
        if change:
            change(files)
        rows = []
        for name, data in files.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
            rows.append([name, 'sha256=' + digest, str(len(data))])
        rows.append([d + '/RECORD', '', ''])
        record = io.StringIO(newline='')
        csv.writer(record, lineterminator='\n').writerows(rows)
        files[d + '/RECORD'] = record.getvalue().encode()
        path = self.root / 'fixture.whl'
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)
        return path

    def test_manually_authored_wheel_has_complete_record_and_source_bytes(self):
        result = checker.check_wheel(self.wheel(), self.root)
        self.assertEqual(result['runtimeFiles'], 1)
        self.assertEqual(result['members'], 7)
        self.assertTrue(result['recordChecked'])

    def test_runtime_changed_even_with_rehashed_record_is_rejected(self):
        artifact = self.wheel(lambda f: f.update({'zonas_geojson/__init__.py': b'wrong\n'}))
        with self.assertRaisesRegex(ValueError, 'bytes de execu'):
            checker.check_wheel(artifact, self.root)

    def test_extra_member_even_with_valid_record_is_rejected(self):
        artifact = self.wheel(lambda f: f.update({'unexpected.txt': b'extra'}))
        with self.assertRaisesRegex(ValueError, 'Lista fechada'):
            checker.check_wheel(artifact, self.root)

    def test_runtime_dependency_is_rejected(self):
        artifact = self.wheel(lambda f: f.__setitem__(checker.DISTINFO + '/METADATA', f[checker.DISTINFO + '/METADATA'].replace(b'\n\n', b'\nRequires-Dist: unexpected\n\n')))
        with self.assertRaisesRegex(ValueError, 'depend'):
            checker.check_wheel(artifact, self.root)

    def test_source_selection_excludes_context_and_runtime_environments(self):
        (self.root / 'CONTRACT.md').write_text('local context')
        (self.root / 'local').mkdir()
        (self.root / 'local/private.json').write_text('{}')
        (self.root / '.venv-check').mkdir()
        (self.root / '.venv-check/config.py').write_text('private')
        self.assertEqual(set(checker.source_files(self.root)), {'LICENSE', 'src/zonas_geojson/__init__.py'})

    def test_unsafe_archive_paths_are_rejected(self):
        for name in ['/absolute', '../escape', 'a/../b', 'a\\b', '.impeccable/state.json', 'local/data.json', '.env', '.venv/bin/python']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.safe_name(name)


if __name__ == '__main__':
    unittest.main()
