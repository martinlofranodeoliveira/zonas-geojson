#!/usr/bin/env python3
"""Confira os artefatos e exercite a instalação fora da árvore de fontes."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[1]
NAME = 'zonas_geojson'
VERSION = '1.0.0'
DISTINFO = f'{NAME}-{VERSION}.dist-info'
ROOT_FILES = {
    '.gitattributes', '.gitignore', 'LICENSE', 'README.md', 'MANIFEST.in',
    'pyproject.toml', 'requirements-build.txt', 'requirements-interop.txt',
}
SUFFIXES = {
    'src': {'.py', '.geojson'}, 'tests': {'.py'}, 'scripts': {'.py'},
    'tools': {'.py'}, 'docs': {'.md', '.json', '.png', '.svg'},
    'fixtures': {'.json', '.gpkg', '.zip'}, '.github': {'.yml'},
}
FORBIDDEN = {'.git', 'local', 'dist', 'build', '__pycache__', '.openai', '.impeccable', 'node_modules'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_name(name):
    p = PurePosixPath(name)
    require(bool(name) and not p.is_absolute() and str(p) == name and '\\' not in name,
            'Nome de arquivo não canônico no artefato.')
    require(all(x not in FORBIDDEN and x not in {'.', '..'} and not x.startswith(('.venv', '.env')) for x in p.parts),
            'Contexto local não pode integrar a distribuição.')
    return p


def source_files(root=ROOT):
    result = {}
    for p in root.rglob('*'):
        rel = p.relative_to(root)
        if any(x in FORBIDDEN or x.startswith(('.venv', '.env')) for x in rel.parts):
            continue
        if not p.is_file():
            continue
        name = rel.as_posix()
        eligible = name in ROOT_FILES or (rel.parts[0] in SUFFIXES and p.suffix in SUFFIXES[rel.parts[0]])
        if eligible:
            require(not p.is_symlink(), 'Fonte pública não pode ser link simbólico.')
            result[name] = p.read_bytes()
    return result


def runtime_files(root=ROOT):
    package = root / 'src' / NAME
    return {p.relative_to(root / 'src').as_posix(): p.read_bytes()
            for p in package.rglob('*') if p.is_file() and p.suffix in {'.py', '.geojson'}}


def check_wheel(path, root=ROOT):
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, 'CRC do wheel inválido.')
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Membro repetido no wheel.')
        for name in names:
            safe_name(name)
            require(not name.endswith('/'), 'Diretório inesperado no wheel.')
        files = {name: archive.read(name) for name in names}
    runtime = runtime_files(root)
    metadata_names = {f'{DISTINFO}/{name}' for name in ['METADATA', 'WHEEL', 'entry_points.txt', 'top_level.txt', 'RECORD']}
    metadata_names.add(f'{DISTINFO}/licenses/LICENSE')
    require(set(files) == set(runtime) | metadata_names, 'Lista fechada do wheel diverge.')
    for name, expected in runtime.items():
        require(files[name] == expected, 'O wheel alterou bytes de execução.')
    require(files[f'{DISTINFO}/licenses/LICENSE'] == (root / 'LICENSE').read_bytes(), 'Licença do wheel divergente.')
    metadata = BytesParser().parsebytes(files[f'{DISTINFO}/METADATA'])
    require(metadata['Name'] == 'zonas-geojson' and metadata['Version'] == VERSION, 'Identidade do wheel divergente.')
    require(metadata['Requires-Python'] == '>=3.11' and metadata['License-Expression'] == 'MIT', 'Metadados de plataforma/licença divergentes.')
    require(not metadata.get_all('Requires-Dist'), 'O runtime não deve ter dependências externas.')
    require('Root-Is-Purelib: true' in files[f'{DISTINFO}/WHEEL'].decode(), 'Wheel deve ser Python puro.')
    require('zonas-geojson = zonas_geojson.cli:main' in files[f'{DISTINFO}/entry_points.txt'].decode(), 'Entry point ausente.')
    rows = list(csv.reader(io.StringIO(files[f'{DISTINFO}/RECORD'].decode())))
    require(len(rows) == len(files) and {r[0] for r in rows} == set(files), 'Cobertura RECORD divergente.')
    for name, digest, size in rows:
        if name.endswith('/RECORD'):
            require(digest == size == '', 'RECORD não deve assinar a si próprio.')
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b'=').decode()
            require(digest == 'sha256=' + expected and size == str(len(files[name])), 'Hash/tamanho RECORD inválido.')
    return {'kind': 'wheel', 'members': len(files), 'runtimeFiles': len(runtime), 'recordChecked': True}


def check_sdist(path, root=ROOT):
    prefix = f'{NAME}-{VERSION}/'
    files = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            require(member.isfile() and member.name.startswith(prefix), 'Tipo/caminho inesperado no sdist.')
            name = member.name[len(prefix):]
            safe_name(name)
            require(name not in files, 'Membro repetido no sdist.')
            files[name] = archive.extractfile(member).read()
    source = source_files(root)
    metadata = {'PKG-INFO', 'setup.cfg'} | {f'src/{NAME}.egg-info/{n}' for n in ['PKG-INFO', 'SOURCES.txt', 'dependency_links.txt', 'entry_points.txt', 'top_level.txt']}
    require(set(files) == set(source) | metadata, 'Lista fechada do sdist diverge da fonte pública.')
    for name, expected in source.items():
        require(files[name] == expected, 'O sdist alterou os bytes da fonte.')
    info = BytesParser().parsebytes(files['PKG-INFO'])
    require(info['Name'] == 'zonas-geojson' and info['Version'] == VERSION, 'Identidade do sdist divergente.')
    return {'kind': 'sdist', 'members': len(files), 'sourceFiles': len(source), 'sourceBytesChecked': True}


def run(args, *, cwd, env):
    p = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    require(p.returncode == 0, 'Um comando de instalação/conferência falhou: ' + (p.stderr[-1400:] or p.stdout[-1400:]))
    return p.stdout


def check_install(path):
    artifact = path.resolve()
    with tempfile.TemporaryDirectory(prefix='zonas-install-') as temporary:
        temporary = Path(temporary)
        env_dir = temporary / 'env'
        venv.EnvBuilder(with_pip=True).create(env_dir)
        bindir = env_dir / ('Scripts' if os.name == 'nt' else 'bin')
        python = bindir / ('python.exe' if os.name == 'nt' else 'python')
        cli = bindir / ('zonas-geojson.exe' if os.name == 'nt' else 'zonas-geojson')
        environment = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
        environment.update(PYTHONUTF8='1', PYTHONNOUSERSITE='1')
        if artifact.name.endswith('.tar.gz'):
            run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', 'setuptools==84.2.1'], cwd=temporary, env=environment)
        run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '--no-build-isolation', str(artifact)], cwd=temporary, env=environment)
        location = run([str(python), '-c', 'import zonas_geojson; print(zonas_geojson.__file__)'], cwd=temporary, env=environment).strip()
        require(Path(location).resolve().is_relative_to(env_dir), 'A importação não veio da instalação isolada.')
        require(run([str(cli), '--version'], cwd=temporary, env=environment).strip().endswith(VERSION), 'Versão instalada divergente.')
        bundle = temporary / 'exemplo.zip'
        run([str(cli), 'exemplo', '--saida', str(bundle)], cwd=temporary, env=environment)
        first = bundle.read_bytes()
        require(zipfile.is_zipfile(io.BytesIO(first)), 'CLI instalada não gerou ZIP.')
        validation = run([str(python), str(ROOT / 'tools/check_bundle.py'), str(bundle), '--demo'], cwd=temporary, env=environment)
        refused = subprocess.run([str(cli), 'exemplo', '--saida', str(bundle)], cwd=temporary, env=environment, capture_output=True)
        require(refused.returncode == 2 and bundle.read_bytes() == first, 'Repetição não preservou destino existente.')
        return {'kind': 'installed-' + ('wheel' if artifact.suffix == '.whl' else 'sdist'), 'outsideSource': True,
                'demoBytes': len(first), 'existingDestinationPreserved': True, 'independentReader': json.loads(validation)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dist', type=Path)
    group.add_argument('--install', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.install:
            result = check_install(args.install)
        else:
            wheel = args.dist / f'{NAME}-{VERSION}-py3-none-any.whl'
            sdist = args.dist / f'{NAME}-{VERSION}.tar.gz'
            result = [check_wheel(wheel), check_sdist(sdist)]
    except (ValueError, OSError, zipfile.BadZipFile, tarfile.TarError, KeyError, json.JSONDecodeError) as error:
        print(f'Distribuição recusada: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
