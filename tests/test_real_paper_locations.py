"""Location gates based on visually reviewed occurrences, not bbox counts."""
import json
from pathlib import Path
import pytest

from tools.audit_formula_mapping import DEFAULT_PAPERS, _load_tex_files
from backend.extractor.arxiv_tex import _merge_tex_files, parse_tex_formulas, build_line_index, resolve_candidate_bbox, compute_section_ordinals
from backend.main import _RESOLVED_FIELDS
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas

PROBES = json.loads((Path(__file__).resolve().parents[1] / 'testdata/benchmarks/location_probes.json').read_text())['probes']


@pytest.mark.parametrize('probe', PROBES, ids=[p['paper'] for p in PROBES])
def test_manually_reviewed_formula_location(probe):
    spec = DEFAULT_PAPERS[probe['paper']]
    if not spec['src'].exists() or not spec['pdf'].exists():
        pytest.skip('Restore external fixtures with python tools/fetch_paper_fixtures.py')
    candidates = parse_tex_formulas(_merge_tex_files(_load_tex_files(spec['src'])))
    lines = build_line_index(spec['pdf'].read_bytes(), candidates)
    ordinals = compute_section_ordinals(candidates)
    index = build_index(candidates)
    def resolve(i, indexed):
        resolve_candidate_bbox(candidates[i], ordinals[i], lines)
        for name in _RESOLVED_FIELDS:
            setattr(indexed, name, getattr(candidates[i], name))
    matches = search_formulas(probe['query'], index, on_candidate_matched=resolve)
    expected = probe['bbox']
    assert any(m.page == probe['page'] and m.bbox is not None and
               all(abs(actual - wanted) < 1.0 for actual, wanted in zip(m.bbox, expected))
               for m in matches), (probe, [(m.page, m.bbox) for m in matches])
