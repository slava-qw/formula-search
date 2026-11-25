"""Render editable publication layouts with genuine product captures.

Run record_demo.py --out docs/presentation/assets --scale 2 first. This tool
renders the original example PDF and HTML/SVG artwork; it never redraws the UI.
Requires development dependencies and installed Chrome. Outputs docs/media.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT/'docs/presentation'
MEDIA = ROOT/'docs/media'


def main():
    import pymupdf
    from playwright.sync_api import sync_playwright

    MEDIA.mkdir(parents=True, exist_ok=True)
    assets = PROJECT/'assets'
    with pymupdf.open(ROOT/'examples/formula-playground.pdf') as document:
        # Direct renders from the checked-in PDF, with no substitute typesetting.
        score = pymupdf.Rect(258, 267, 346, 310)
        document[0].get_pixmap(matrix=pymupdf.Matrix(8,8),clip=score).save(assets/'equation-score.png')
        document[0].get_pixmap(matrix=pymupdf.Matrix(8,8),clip=pymupdf.Rect(240,416,399,447)).save(assets/'equation-objective.png')
        document[0].get_pixmap(matrix=pymupdf.Matrix(2,2)).save(assets/'paper-page1.png')
        document[1].get_pixmap(matrix=pymupdf.Matrix(2,2)).save(assets/'paper-page2.png')
    for name in ['search-light.png','two-pages.png','bookmarks.png','pdf-excerpt.png',
                 'native-pdf.png','search-dark.png','capture.json']:
        shutil.copyfile(assets/name, MEDIA/name)
    shutil.copyfile(assets/'demo.mp4',MEDIA/'walkthrough.mp4')
    shutil.copyfile(assets/'demo.vtt',MEDIA/'walkthrough.vtt')
    # Reusable vector lockup; the primary README artwork is overview.png.
    (MEDIA/'hero.svg').write_text('''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="260" viewBox="0 0 1600 260" role="img" aria-labelledby="t d">
<title id="t">Formula Search</title><desc id="d">Find the formula. Stay in the paper. Local notation search for PDFs and arXiv.</desc>
<rect width="1600" height="260" fill="#eef4f2"/><path d="M65 196H1535" stroke="#c8d8d1"/>
<text x="64" y="104" fill="#142d29" font-family="Segoe UI,Arial,sans-serif" font-size="62" font-weight="700" letter-spacing="-2">Formula Search</text>
<text x="66" y="157" fill="#086f64" font-family="Segoe UI,Arial,sans-serif" font-size="30">Find the formula. Stay in the paper.</text>
<text x="66" y="230" fill="#526961" font-family="Segoe UI,Arial,sans-serif" font-size="19">MATHEMATICAL NOTATION SEARCH / CHROME + LOCAL PYTHON BACKEND</text>
</svg>''',encoding='utf-8',newline='\n')
    sizes = {'hero':('overview.png',1600,1000), 'workflow':('workflow.png',1600,1000),
             'architecture':('architecture.png',1600,780), 'poster':('demo-poster.png',1920,1080),
             'social':('social-preview.png',1280,640)}
    report = {'source':'docs/presentation/stills.html','renders':[],'errors':[]}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome')
        for view,(filename,width,height) in sizes.items():
            page = browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
            page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.goto((PROJECT/'stills.html').as_uri()+f'?view={view}')
            page.evaluate('document.fonts.ready')
            page.locator('img:visible').evaluate_all('(images)=>Promise.all(images.map(image=>image.decode()))')
            assert page.evaluate('document.fonts.check("700 32px Inter")')
            assert page.evaluate('document.documentElement.scrollWidth') == width
            page.screenshot(path=str(MEDIA/filename))
            report['renders'].append({'view':view,'file':filename,'width':width,'height':height})
            page.close()
        browser.close()
    assert not report['errors'], report
    (MEDIA/'artwork.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    ledger=[]
    for path in sorted(assets.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix in {'.woff2','.txt'} and 'fonts' in path.parts:
            origin='Inter project; supplied authoring-tool font bundle'
            license_name='SIL OFL 1.1 (assets/fonts/OFL-inter.txt)'
        elif path.name.startswith(('paper-page','equation-')):
            origin='Direct PyMuPDF render of examples/formula-playground.pdf'
            license_name='Project MIT; renderer has separate dependency terms'
        elif path.name=='icon.png':
            origin='Existing extension/images/icon.png'
            license_name='Project MIT'
        elif path.suffix in {'.png','.mp4','.vtt','.json'}:
            origin='Real Chrome extension/backend capture; see assets/capture.json and edit.json'
            license_name='Project MIT'
        else:
            origin='Original presentation source'
            license_name='Project MIT'
        ledger.append({'path':path.relative_to(PROJECT).as_posix(),'bytes':path.stat().st_size,
                       'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                       'origin':origin,'license':license_name})
    (PROJECT/'assets.json').write_text(json.dumps({'generated_ui':False,
        'source_pdf':'examples/formula-playground.pdf','files':ledger},indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
