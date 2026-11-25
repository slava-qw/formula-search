"""Check public docs, release contents, asset integrity and obvious secret markers.

Read-only and offline. This is a focused release check, not a security audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path):
    sys.path.insert(0,str(root/'tools'))
    import prepare_github
    prepare_github.ROOT = root
    paths = prepare_github.selected_files()
    errors = []
    for path in paths:
        if not path.is_file():
            errors.append(f'Missing required file: {path.relative_to(root)}')
    for path in paths:
        if not path.is_file():
            continue
        if path.suffix.lower() == '.md':
            source = path.read_text(encoding='utf-8')
            links = re.findall(r'\]\(([^)]+)\)|(?:src|href)="([^"]+)"',source)
            for groups in links:
                link = next(g for g in groups if g).split(' "')[0].strip('<>')
                if re.match(r'^(?:[a-z][a-z0-9+.-]*:|#)',link,re.I):
                    continue
                target = unquote(link.split('#')[0])
                if target and not (path.parent/target).resolve().exists():
                    errors.append(f'Broken link: {path.relative_to(root)} -> {target}')
        if path.suffix.lower() in {'.py','.js','.cjs','.json','.md','.yml','.yaml','.txt','.html','.ps1'}:
            source = path.read_text(encoding='utf-8-sig')
            if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{50,}|ghp_[A-Za-z0-9]{36}|AKIA[A-Z0-9]{16}',source):
                errors.append(f'Possible secret marker: {path.relative_to(root)}')
    assets = json.loads((root/'licenses/katex-assets.json').read_text())['files']
    for item in assets:
        path = root/item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            errors.append(f'Bundled asset differs from verified upstream bytes: {item["path"]}')
    manifest = root/'PUBLICATION_MANIFEST.json'
    if manifest.is_file():
        recorded = json.loads(manifest.read_text())['files']
        for item in recorded:
            path = root/item['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                errors.append(f'Snapshot differs from publication manifest: {item["path"]}')
        # A GitHub checkout has .git and developers can create local environments.
        # Check the published payload, not unrelated ignored local directories.
        for item in recorded:
            relative=item['path']
            for part in ['.git','.claude','.codex','.formula_cache','.venv','.venv-ocr','extension/viewer','testdata/papers']:
                if relative==part or relative.startswith(part+'/'):
                    errors.append(f'Local-only path recorded in publication manifest: {relative}')
    for needed in ['LICENSE','THIRD_PARTY_NOTICES.md','extension/popup/lib/LICENSE',
                   'docs/media/hero.svg','docs/media/search-light.png','docs/media/overview.png',
                   'docs/media/social-preview.png','docs/media/demo.mp4','docs/media/demo.vtt',
                   'docs/media/demo-preview.gif','docs/media/demo-poster.png','docs/media/workflow.png',
                   'docs/media/architecture.png','docs/media/walkthrough.mp4',
                   'docs/presentation/review.html','docs/presentation/edit.json','docs/presentation/assets.json',
                   'examples/formula-playground.pdf','testdata/paper-fixtures.json']:
        if not (root/needed).is_file():
            errors.append(f'Missing publication asset: {needed}')
    ledger_path=root/'docs/presentation/assets.json'
    if ledger_path.is_file():
        for item in json.loads(ledger_path.read_text(encoding='utf-8'))['files']:
            path=ledger_path.parent/item['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                errors.append(f'Presentation input differs from its provenance ledger: {item["path"]}')
    edit_path=root/'docs/presentation/edit.json'
    if edit_path.is_file():
        edit=json.loads(edit_path.read_text(encoding='utf-8'))
        source=edit_path.parent/edit['source']
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=edit['source_sha256']:
            errors.append('Presentation edit times refer to a different product recording.')
    media_receipt=root/'docs/media/presentation-checks.json'
    if media_receipt.is_file():
        for item in json.loads(media_receipt.read_text(encoding='utf-8'))['files']:
            path=root/item['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                errors.append(f'Presentation output differs from its verification receipt: {item["path"]}')
    receipt_path=root/'docs/media/publication-checks.json'
    if receipt_path.is_file():
        receipt=json.loads(receipt_path.read_text())
        expected=receipt.get('production_sha256')
        if expected:
            digest=hashlib.sha256()
            for path in paths:
                relative=path.relative_to(root).as_posix()
                if path.parts[len(root.parts)] in {'backend','extension'} and path.is_file():
                    digest.update(relative.encode()+b'\0'+path.read_bytes()+b'\0')
            if digest.hexdigest()!=expected:
                errors.append('Production files differ from the publication verification receipt.')
    report = {'files_checked':len(paths),'upstream_assets_verified':len(assets),'errors':errors,
              'scope':'Local links, required contents, upstream KaTeX hashes, known secret patterns, snapshot hashes.'}
    print(json.dumps(report,indent=2))
    return bool(errors)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    raise SystemExit(check(parser.parse_args().root.resolve()))
