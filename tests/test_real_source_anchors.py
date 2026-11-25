"""Visually reviewed source occurrence locations, with pinned input bytes."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.extractor import arxiv_tex as tex
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index

ROOT = Path(__file__).resolve().parents[1]
PROBES = json.loads((ROOT/'testdata/benchmarks/source_inline_probes.json').read_text())['papers']


@pytest.mark.skipif(os.environ.get('RUN_PAPER_AUDITS') != '1', reason='set RUN_PAPER_AUDITS=1 for real source location gates')
@pytest.mark.parametrize('probe', PROBES, ids=lambda p: p['paper'])
def test_reviewed_source_occurrence_bounds(probe):
    pdf_path, source_path = ROOT/probe['pdf_path'], ROOT/probe['source_path']
    if not pdf_path.exists() or not source_path.exists():
        pytest.skip('Pinned paper fixture is not downloaded')
    pdf, source = pdf_path.read_bytes(), source_path.read_bytes()
    assert hashlib.sha256(pdf).hexdigest() == probe['hashes']['pdf']
    assert hashlib.sha256(source).hexdigest() == probe['hashes']['source']
    candidates = tex.parse_tex_formulas(tex._unpack_tex_source(source))
    identities = build_index(candidates)
    index = tex.build_line_index(pdf, candidates)
    ordinals = tex.compute_section_ordinals(candidates)
    for expected in probe['locations']:
        i = expected['index']
        candidate = candidates[i]
        assert identities[i].occurrence_id == expected['occurrence_id']
        assert candidate.latex == expected['latex']
        assert (candidate.source_file, candidate.source_line) == (expected['source_file'], expected['source_line'])
        tex.resolve_candidate_bbox(candidate, ordinals[i], index)
        assert candidate.page == expected['page'], expected
        assert candidate.bbox is not None, expected
        assert all(abs(actual-wanted) < 1.0 for actual,wanted in zip(candidate.bbox, expected['bbox'])), expected
    for forbidden in probe.get('forbidden_locations', []):
        i = forbidden['index']
        candidate = candidates[i]
        assert identities[i].occurrence_id == forbidden['occurrence_id']
        assert candidate.latex == forbidden['latex']
        tex.resolve_candidate_bbox(candidate, ordinals[i], index)
        if forbidden.get('require_unverified'):
            assert candidate.bbox is None and candidate.bbox_quality in {'section-estimate','unlocated'}, forbidden
        query = forbidden.get('query', candidate.latex)
        matches = search_formulas(query, build_index([candidate]), mode='contains' if 'query' in forbidden else 'exact')
        assert len(matches) == 1
        match = matches[0]
        if probe['paper'] == '2312.00752v2' and candidate.source_line == 34:
            # Missing subspan rectangles alone did not fix the wrong equation.
            # It must no longer be represented as a verified page/line mapping.
            assert candidate.bbox_quality in {'section-estimate', 'unlocated'} or candidate.page != 26
        if match.page == forbidden['page'] and match.bbox is not None:
            a, b = match.bbox, forbidden['bbox']
            assert min(a[2],b[2]) <= max(a[0],b[0]) or min(a[3],b[3]) <= max(a[1],b[1]), forbidden
