"""Build a clean local source snapshot and unpacked-extension ZIP. Never publishes.

The destination must not exist. The working repository and its history are left
intact. Run this again with a new destination after changing release files.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = {'SETUP.md', 'USAGE.md', 'ARCHITECTURE.md', 'VALIDATION.md',
               'PRIVACY.md', 'PUBLISHING.md', 'MEDIA.md', 'RELEASE_NOTES.md'}
TOOL_ROOTS = {'prepare_github', 'check_publication', 'record_demo', 'make_publication_graphics', 'finish_presentation', 'fetch_paper_fixtures',
              'check_extension', 'run_js_test', 'smoke_search_browser',
              'benchmark_completion', 'verify_real_ocr', 'audit_dependencies'}
ROOT_FILES = {'README.md', 'CONTRIBUTING.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md',
              'LICENSE', 'LICENSE.md', '.gitignore', '.gitattributes', '.editorconfig',
              'pytest.ini', 'start.ps1', 'requirements.txt', 'requirements-dev.txt',
              'requirements-ocr.txt', 'requirements-core-lock.txt', 'requirements-ocr-lock.txt'}


def public_tools():
    """Include runtime imports of supported maintenance tools, not old experiments."""
    pending, found = list(TOOL_ROOTS), set()
    for test in (ROOT/'tests').glob('*.py'):
        for node in ast.walk(ast.parse(test.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('tools.'):
                pending.append(node.module.split('.')[1])
    while pending:
        name = pending.pop()
        if name in found:
            continue
        path = ROOT/'tools'/f'{name}.py'
        if not path.is_file():
            raise FileNotFoundError(path)
        found.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('tools.'):
                pending.append(node.module.split('.')[1])
    return {f'tools/{name}.py' for name in found}


def selected_files():
    paths = {ROOT/name for name in ROOT_FILES if (ROOT/name).is_file()}
    for folder in ['backend', 'extension', 'tests', 'tests_js', '.github', 'licenses', 'examples']:
        paths.update(p for p in (ROOT/folder).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.log', '.aux'}
                     and not (folder == 'extension' and 'viewer' in p.relative_to(ROOT).parts))
    paths.update(ROOT/name for name in public_tools())
    paths.update((ROOT/'tools').glob('*.js'))
    paths.update(ROOT/'docs'/name for name in PUBLIC_DOCS)
    paths.update(p for p in (ROOT/'docs/media').glob('*') if p.is_file()
                 and p.suffix in {'.png', '.svg', '.gif', '.mp4', '.vtt', '.json'})
    # Editable presentation sources and captured inputs, without Studio history,
    # generated renders, assistant setup files or Node environments.
    presentation = ROOT/'docs/presentation'
    paths.update(p for p in presentation.rglob('*') if p.is_file()
                 and p.suffix in {'.html','.css','.md','.json','.png','.woff2','.txt','.mp4','.vtt'}
                 and not any(part in {'.hyperframes','node_modules','renders'} for part in p.relative_to(presentation).parts)
                 and p.name not in {'AGENTS.md','CLAUDE.md'})
    paths.update(p for p in (ROOT/'testdata').rglob('*.json') if 'artifacts' not in p.parts)
    paths.add(ROOT/'testdata/README.md')
    return sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix())


def build(destination: Path):
    destination = destination.resolve()
    if destination == ROOT or destination in ROOT.parents:
        raise ValueError('Choose a new output directory, not the repository or its parent.')
    if destination.exists():
        raise FileExistsError(f'Destination already exists; choose a new one: {destination}')
    archive = destination.parent/f'{destination.name}-source.zip'
    extension_archive = destination.parent/f'{destination.name}-extension.zip'
    if archive.exists() or extension_archive.exists():
        raise FileExistsError('An output archive already exists; choose a new destination name.')
    files = selected_files()
    missing = [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(', '.join(missing))
    version = json.loads((ROOT/'extension/manifest.json').read_text())['version']
    revision = subprocess.check_output(['git', '-c', f'safe.directory={ROOT.as_posix()}',
        'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() if (ROOT/'.git').exists() else None
    destination.mkdir(parents=True)
    records = []
    for source in files:
        relative = source.relative_to(ROOT)
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        data = target.read_bytes()
        records.append({'path': relative.as_posix(), 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'project': 'Formula Search', 'version': version, 'base_revision': revision,
        'snapshot': 'Current selected working-tree files, including uncommitted publication changes.',
        'excluded': ['Git history', 'local assistant settings', 'runtime caches', 'environments',
                     'downloaded research papers', 'development logs and bulk audit artifacts',
                     'unused legacy PDF.js viewer', 'historical experiment tools'], 'files': records}
    (destination/'PUBLICATION_MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8', newline='\n')
    # Exclusive create prevents replacing an existing archive.
    for output, base in [(archive, destination), (extension_archive, destination/'extension')]:
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(base.rglob('*')):
                if path.is_file():
                    bundle.write(path, path.relative_to(base).as_posix())
    return {'directory': str(destination), 'source_zip': str(archive),
            'extension_zip': str(extension_archive), 'files': len(records),
            'source_bytes': sum(r['bytes'] for r in records)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.out), indent=2))
