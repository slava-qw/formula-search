"""Offline PDF-only extraction/search audit with reviewed location gates.

The baseline repeats the pre-0.10.4 marker detector. Counts describe fragments,
not equation recall. PDF source archives, the network and persistent caches are
not used. Timings are single-run local wall times, not service latency claims.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fitz
from backend.extractor.text_layer import FormulaCandidate, extract_formulas
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas
from tools.audit_formula_mapping import DEFAULT_PAPERS

PROBE_DATA = json.loads((ROOT / 'testdata/benchmarks/pdf_text_probes.json').read_text(encoding='utf-8'))
PROBES = PROBE_DATA['probes']
NEGATIVE_PROBES = PROBE_DATA['negative_probes']
EXCLUDED_REGIONS = PROBE_DATA.get('excluded_regions', [])


def overlaps(box, region):
    return box and min(box[2], region[2]) > max(box[0], region[0]) and min(box[3], region[3]) > max(box[1], region[1])


def legacy_extract(pdf):
    """Original marker-only line detection and block fallback, including flags."""
    candidates = []
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        for n, page in enumerate(doc):
            texts = [''.join(s.get('text', '') for s in line.get('spans', ())).strip()
                     for block in page.get_text('dict').get('blocks', ()) if block.get('type') == 0
                     for line in block.get('lines', ())]
            selected = [t for t in texts if any(ch in t for ch in '$\\^_')]
            if not selected:
                selected = [b[4].strip() for b in page.get_text('blocks')
                            if len(b) == 7 and b[6] == 0 and any(ch in b[4] for ch in '$\\^_')]
            candidates.extend(FormulaCandidate(t, 'text', page=n) for t in selected)
    return candidates


def run(out, baseline_candidates=None):
    out.mkdir(parents=True, exist_ok=True)
    specs = {key: value['pdf'] for key, value in DEFAULT_PAPERS.items()}
    manifest = json.loads((ROOT / 'testdata/benchmarks/manifest.json').read_text())['papers']
    specs.update({key: ROOT / p['artifacts']['pdf']['path'] for key, p in manifest.items()})
    specs['2506.01320'] = ROOT / 'testdata/papers/2506.01320.pdf'
    report = {'method': __doc__, 'papers': [], 'location_checks': [], 'negative_checks': [], 'location_negative_checks': [], 'exclusion_checks': []}
    baseline = json.loads(baseline_candidates.read_text(encoding='utf-8')) if baseline_candidates else []
    for key, path in specs.items():
        pdf = path.read_bytes()
        digest = hashlib.sha256(pdf).hexdigest()
        if key in manifest:
            assert digest == manifest[key]['artifacts']['pdf']['sha256']
        started = time.perf_counter()
        old = legacy_extract(pdf)
        old_ms = (time.perf_counter()-started)*1000
        started = time.perf_counter()
        candidates = extract_formulas(pdf)
        extract_ms = (time.perf_counter()-started)*1000
        started = time.perf_counter()
        index = build_index(candidates)
        index_ms = (time.perf_counter()-started)*1000
        assert all(len(c.pdf_latex_chars) == len(c.latex) for c in candidates)
        assert all(''.join(ch for ch, _ in c.pdf_latex_chars) == c.latex for c in candidates)
        assert all(c.bbox and 0 <= c.bbox[0] < c.bbox[2] <= c.page_w + 1
                   and 0 <= c.bbox[1] < c.bbox[3] <= c.page_h + 1 for c in candidates)
        row = {'paper': key, 'pdf': str(path.relative_to(ROOT)), 'sha256': digest,
               'legacy_candidates': len(old), 'fragments': len(candidates),
               'fraction_commands': sum(c.latex.count(r'\frac{') for c in candidates),
               'radical_commands': sum(c.latex.count(r'\sqrt{') for c in candidates),
               'legacy_extract_ms': round(old_ms, 3), 'extract_ms': round(extract_ms, 3),
               'index_ms': round(index_ms, 3), 'probes': []}
        for probe in [p for p in PROBES if p['paper'] == key]:
            assert probe['sha256'] == digest
            started = time.perf_counter()
            matches = search_formulas(probe['query'], index)
            search_ms = (time.perf_counter()-started)*1000
            expected = probe['bbox']
            checked = next((m for m in matches if m.page == probe['page'] and m.bbox and
                            all(abs(a-b)<1 for a,b in zip(m.bbox, expected))), None)
            if checked is None:
                raise AssertionError((probe, [(m.page, m.bbox, m.latex) for m in matches]))
            result = {'query': probe['query'], 'matches': len(matches), 'search_ms': round(search_ms, 3),
                      'legacy_matches': len(search_formulas(probe['query'], build_index(old))),
                      'checked_page': checked.page, 'checked_bbox': checked.bbox,
                      'checked_latex': checked.latex, 'bbox_source': checked.bbox_source}
            row['probes'].append(result)
            report['location_checks'].append({'paper': key, **result})
            for wrong_query in probe.get('reject_queries', []):
                wrong_at_location = [m for m in search_formulas(wrong_query, index) if m.page == probe['page']
                                     and m.bbox and all(abs(a-b)<1 for a,b in zip(m.bbox, expected))]
                assert not wrong_at_location, (probe, wrong_query)
                report['location_negative_checks'].append({'paper': key, 'query': wrong_query, 'page': probe['page'], 'bbox': expected, 'matches': 0})
            with fitz.open(stream=pdf, filetype='pdf') as doc:
                page = doc[checked.page]
                rect = fitz.Rect(checked.bbox)
                page.draw_rect(rect, color=(1, .2, .1), fill=(1, .9, 0), fill_opacity=.25, width=.6)
                clip = fitz.Rect(max(0, rect.x0-90), max(0, rect.y0-35),
                                 min(page.rect.width, rect.x1+90), min(page.rect.height, rect.y1+35))
                page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip).save(out / f'{key}-{len(row["probes"])}.png')
        for probe in (p for p in NEGATIVE_PROBES if p['paper'] == key):
            assert not search_formulas(probe['query'], index), probe
            report['negative_checks'].append({**probe, 'matches': 0})
        for region_number, region in enumerate((p for p in EXCLUDED_REGIONS if p['paper'] == key), 1):
            assert region['sha256'] == digest
            assert not any(c.page == region['page'] and overlaps(c.bbox, region['bbox']) for c in candidates), region
            before = [c for c in baseline if c['paper'] == key and c['page'] == region['page'] and overlaps(c['bbox'], region['bbox'])]
            report['exclusion_checks'].append({**region, 'candidates': 0, 'provided_baseline_candidates': before})
            with fitz.open(stream=pdf, filetype='pdf') as doc:
                page = doc[region['page']]
                page.draw_rect(fitz.Rect(region['bbox']), color=(1, .2, .1), width=.5)
                suffix = '' if region_number == 1 else f'-{region_number}'
                page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(region['bbox'])+(-4, -4, 4, 4)).save(out/f'{key}-excluded{suffix}.png')
        report['papers'].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline-candidates', type=Path)
    args = parser.parse_args()
    run(args.out, args.baseline_candidates)
