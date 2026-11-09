#!/usr/bin/env python3
"""Prepare exemplo e checksums a partir de uma árvore própria limpa e testada."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from check_distribution import ROOT, check_sdist, check_wheel, require, runtime_files


def command(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    require(result.returncode == 0, 'Comando de conferência recusado: ' + (result.stderr[-1200:] or result.stdout[-1200:]))
    return result.stdout


def clean_head():
    require(Path(command(['git', 'rev-parse', '--show-toplevel']).strip()).resolve() == ROOT,
            'Use uma árvore Git própria na raiz do projeto.')
    require(not command(['git', 'status', '--porcelain=v1', '--untracked-files=all']),
            'A árvore Git deve estar limpa e commitada.')
    return command(['git', 'rev-parse', 'HEAD']).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=ROOT / 'dist')
    args = parser.parse_args(argv)
    try:
        commit = clean_head()
        import zonas_geojson
        installed = Path(zonas_geojson.__file__).resolve().parent.parent
        require(zonas_geojson.__version__ == '1.0.0', 'A versão importada diverge da fonte.')
        for name, expected in runtime_files().items():
            require((installed / name).read_bytes() == expected, 'O módulo importado diverge da fonte atual.')
        destination = args.dist.resolve()
        wheel = destination / 'zonas_geojson-1.0.0-py3-none-any.whl'
        sdist = destination / 'zonas_geojson-1.0.0.tar.gz'
        wheel_proof, source_proof = check_wheel(wheel), check_sdist(sdist)
        example = destination / 'exemplo-zonas.zip'
        sums = destination / 'SHA256SUMS'
        require(not example.exists() and not example.is_symlink() and not sums.exists() and not sums.is_symlink(),
                'Exemplo/checksums já existem; preserve os artefatos de outra preparação.')
        command([sys.executable, '-m', 'zonas_geojson', 'exemplo', '--saida', str(example)])
        proof = json.loads(command([sys.executable, str(ROOT / 'tools/check_bundle.py'), str(example), '--demo']))
        assets = []
        for path in sorted([wheel, sdist, example]):
            data = path.read_bytes()
            assets.append({'name': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        require(clean_head() == commit, 'A fonte mudou durante a preparação.')
        with sums.open('x', encoding='ascii', newline='\n') as stream:
            stream.write(''.join(f"{a['sha256']}  {a['name']}\n" for a in assets))
        print(json.dumps({'commit': commit, 'wheel': wheel_proof, 'source': source_proof,
                          'example': proof, 'assets': assets, 'checksumFile': sums.name}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError, KeyError, json.JSONDecodeError) as error:
        print(f'Preparação recusada: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
