"""Check every PDF-only candidate with the extension's bundled KaTeX.

This measures renderer compatibility, not mathematical fidelity or recall.
Reviewed location probes additionally check query highlights and the visible
MathML delimiters/relations; optional Chromium screenshots show those probes
next to their original PDF crops. No source archive or network is used.
"""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fitz
from backend.extractor.text_layer import extract_formulas
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas
from tools.audit_pdf_text import PROBES
from tools.audit_formula_mapping import DEFAULT_PAPERS

RENDER_JS = r"""
const fs = require('fs'), katex = require('./extension/popup/lib/katex.min.js');
const rows = JSON.parse(fs.readFileSync(0, 'utf8')), errors = [], warnings = [], probes = [];
for (const row of rows) {
  const warn = console.warn;
  console.warn = (...args) => warnings.push({...row, warning: args.join(' ')});
  try {
    const markup = katex.renderToString(row.latex, {throwOnError:true, strict:'ignore', trust:false});
    if (row.query) probes.push({...row, markup, mathml:katex.renderToString(row.latex,
      {throwOnError:true, strict:'ignore', trust:false, output:'mathml'})});
  } catch (error) { errors.push({...row, error: String(error.message)}); }
  finally { console.warn = warn; }
}
process.stdout.write(JSON.stringify({katex:katex.version, errors, warnings, probes}));
"""


def render(rows):
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required to audit the bundled KaTeX renderer')
    result = subprocess.run([node, '-e', RENDER_JS], cwd=ROOT, input=json.dumps(rows),
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=120)
    return json.loads(result.stdout)


def visible_math(markup):
    # Ignore the TeX annotation: it contains braces even when they are invisible.
    root = ET.fromstring(markup)
    tags = {'mi', 'mo', 'mn', 'mtext'}
    return ''.join(node.text or '' for node in root.iter() if node.tag.rsplit('}', 1)[-1] in tags)


def check_variants(markup, variants):
    nodes = list(ET.fromstring(markup).iter())
    for letter, variant in variants.items():
        assert any(n.text == letter and n.attrib.get('mathvariant') == variant for n in nodes), (letter, variant, markup)


def check_accents(markup, accents):
    nodes = list(ET.fromstring(markup).iter())
    for accent in accents:
        matches = [n for n in nodes if n.tag.rsplit('}', 1)[-1] == 'mover' and n.attrib.get('accent') == 'true'
                   and len(n) == 2 and visible_math(ET.tostring(n[0], encoding='unicode')) == accent['base']
                   and visible_math(ET.tostring(n[1], encoding='unicode')) == accent['mark']
                   ]
        assert matches, (accent, markup)
        if script := accent.get('script'):
            assert any(n.tag.rsplit('}', 1)[-1] == 'm'+script['kind'] and len(n) == 2
                       and n[0] in matches and visible_math(ET.tostring(n[1], encoding='unicode')) == script['value']
                       for n in nodes), (accent, markup)


def check_words(markup, roman_words=(), operators=(), limits=()):
    nodes = [n for n in ET.fromstring(markup).iter() if n.tag.rsplit('}', 1)[-1] in ('mi', 'mo', 'mn', 'mtext')]
    normal = ''.join((n.text or '') if n.attrib.get('mathvariant') == 'normal' else '|' for n in nodes)
    for word in roman_words:
        assert word in normal, (word, markup)
    for operator in operators:
        assert any(''.join((n.text or '').split()) == operator and n.tag.rsplit('}', 1)[-1] == 'mi'
                   for n in nodes), (operator, markup)
    # Verify placement and scope, not just the presence of an index somewhere.
    def visible(node):
        return ''.join(visible_math(ET.tostring(node, encoding='unicode')).split()).replace('\u2061', '')
    for limit in limits:
        found = 0
        for node in ET.fromstring(markup).iter():
            tag = node.tag.rsplit('}', 1)[-1]
            if tag not in ('munder', 'mover', 'munderover') or len(node) < 2:
                continue
            if visible(node[0]) != limit['operator']:
                continue
            lower = visible(node[1]) if tag in ('munder', 'munderover') else None
            upper = visible(node[-1]) if tag in ('mover', 'munderover') else None
            if ('lower' not in limit or lower == limit['lower']) and ('upper' not in limit or upper == limit['upper']):
                found += 1
        assert found == limit.get('count', 1), (limit, found, markup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline-candidates', type=Path)
    parser.add_argument('--baseline-label', default='provided candidate snapshot')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    specs = {k: v['pdf'] for k, v in DEFAULT_PAPERS.items()}
    manifest = json.loads((ROOT/'testdata/benchmarks/manifest.json').read_text())['papers']
    specs.update({k: ROOT/v['artifacts']['pdf']['path'] for k, v in manifest.items()})
    specs['2506.01320'] = ROOT/'testdata/papers/2506.01320.pdf'
    rows, papers = [], []
    for paper, path in specs.items():
        data = path.read_bytes()
        candidates = extract_formulas(data)
        digest = hashlib.sha256(data).hexdigest()
        if paper in manifest:
            assert digest == manifest[paper]['artifacts']['pdf']['sha256']
        papers.append({'paper': paper, 'sha256': digest, 'candidates': len(candidates)})
        rows.extend({'paper': paper, 'page': c.page, 'latex': c.latex, 'bbox': c.bbox} for c in candidates)
        index = build_index(candidates)
        for probe in (p for p in PROBES if p['paper'] == paper):
            assert probe['sha256'] == digest
            matches = search_formulas(probe['query'], index)
            found = next((m for m in matches if m.page == probe['page'] and m.bbox and
                          all(abs(a-b)<1 for a, b in zip(m.bbox, probe['bbox']))), None)
            assert found is not None, probe
            row = {'paper': paper, 'page': found.page, 'query': probe['query'],
                   'bbox': found.bbox, 'latex': found.latex,
                   'visible_contains': probe.get('visible_contains', ''),
                   'math_variants': probe.get('math_variants', {}),
                   'math_accents': probe.get('math_accents', []),
                   'roman_words': probe.get('roman_words', []),
                   'math_operators': probe.get('math_operators', []),
                   'operator_limits': probe.get('operator_limits', [])}
            rows.append({**row, 'variant': 'formula'})
            if found.highlighted_latex:
                rows.append({**row, 'variant': 'highlight', 'latex': found.highlighted_latex})
    result = render(rows)
    for probe in result['probes']:
        probe['visible_math'] = visible_math(probe['mathml'])
        assert probe['visible_contains'] in probe['visible_math'], probe
        check_variants(probe['mathml'], probe['math_variants'])
        check_accents(probe['mathml'], probe['math_accents'])
        check_words(probe['mathml'], probe['roman_words'], probe['math_operators'], probe['operator_limits'])
    report = {'method': __doc__, 'katex': result['katex'], 'papers': papers,
              'notation_checks': len(rows), 'errors': result['errors'], 'warnings': result['warnings'],
              'probes': [{k: v for k, v in p.items() if k not in ('markup', 'mathml')} for p in result['probes']]}
    if args.baseline_candidates:
        before = json.loads(args.baseline_candidates.read_text(encoding='utf-8'))
        baseline = render(before)
        report['baseline'] = {'label': args.baseline_label, 'candidates': len(before),
                              'errors': baseline['errors'], 'warnings': baseline['warnings'],
                              'candidates_by_paper': dict(Counter(r['paper'] for r in before))}
    if args.browser:
        from playwright.sync_api import sync_playwright
        by_query = {(p['paper'], p['page'], p['query']): p for p in result['probes']
                    if p['visible_contains'] or p['roman_words'] or p['math_operators']}
        gallery = list(by_query.values())  # prefer highlight when available
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chromium', headless=True)
            page = browser.new_page(viewport={'width': 1100, 'height': 900}, device_scale_factor=1)
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            # Local gallery: the production renderer/fonts, without unrelated
            # popup scripts that require an installed extension context.
            page.goto((ROOT/'extension/popup/lib/katex.min.css').as_uri())
            page.set_content('<html><head></head><body><main id="audit"></main></body></html>')
            page.add_style_tag(url=(ROOT/'extension/popup/lib/katex.min.css').as_uri())
            page.add_script_tag(url=(ROOT/'extension/popup/lib/katex.min.js').as_uri())
            page.add_style_tag(content='body{background:white;color:#14213d;padding:24px;font:16px Arial}')
            for probe in gallery:
                with fitz.open(specs[probe['paper']]) as doc:
                    pdfpage = doc[probe['page']]
                    box = fitz.Rect(probe['bbox'])
                    clip = fitz.Rect(max(0, box.x0-35), max(0, box.y0-15),
                                     min(pdfpage.rect.width, box.x1+35), min(pdfpage.rect.height, box.y1+15))
                    png = pdfpage.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip).tobytes('png')
                page.evaluate('''({probe, image}) => {
                    const row = document.createElement('section');
                    row.style.cssText = 'padding:18px;border-bottom:1px solid #ddd';
                    const title = document.createElement('div');
                    title.textContent = `${probe.paper}, PDF page ${probe.page+1}: ${probe.query}`;
                    const original = document.createElement('img'); original.src = image;
                    original.style.cssText = 'display:block;max-width:100%;margin:8px 0';
                    const rendered = document.createElement('div');
                    katex.render(probe.latex, rendered, {throwOnError:true, strict:'ignore', trust:false});
                    row.append(title, original, rendered); document.querySelector('#audit').append(row);
                }''', {'probe': probe, 'image': 'data:image/png;base64,'+base64.b64encode(png).decode()})
            page.evaluate('() => document.fonts.ready')
            page.screenshot(path=str(args.out/'reviewed-formulas.png'), full_page=True)
            assert page.locator('.katex-error').count() == 0
            report['browser'] = {'version': browser.version, 'reviewed_pairs': len(gallery), 'page_errors': page_errors}
            assert not page_errors, page_errors
            browser.close()
    (args.out/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'candidates': sum(p['candidates'] for p in papers),
                      'notation_checks': len(rows), 'errors': len(result['errors']),
                      'warnings': len(result['warnings']), 'checked_previews': len(result['probes'])}))
    assert not result['errors'], result['errors']


if __name__ == '__main__':
    main()
