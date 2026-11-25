from dataclasses import replace

import httpx
import pytest

from backend import main
from backend.cache import candidate_from_dict, candidate_to_dict
from backend.extractor.arxiv_tex import parse_tex_formulas
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index


def ids(candidates):
    return [item.occurrence_id for item in build_index(candidates)]


def test_same_source_line_occurrences_remain_distinct_after_lazy_mapping_and_cache():
    candidates = parse_tex_formulas('First $x+y$ then $x+y$.', source_file='main.tex')
    before = ids(candidates)
    assert len(before) == len(set(before)) == 2
    assert candidates[0].source_line == candidates[1].source_line == 1
    # Lazy location resolution changes page/rectangle after indexing. Identity
    # must not depend on whether that separate mapping operation has run yet.
    candidates = [replace(item, page=0, bbox=(10, 20+i*100, 50, 40+i*100))
                  for i, item in enumerate(candidates)]
    assert ids(candidates) == before
    assert ids([candidate_from_dict(candidate_to_dict(item)) for item in candidates]) == before


def test_unknown_source_order_repeats_stay_distinct_without_location():
    a = FormulaCandidate('x+y', 'tex', source_file='main.tex', source_line=10)
    before = ids([a, replace(a)])
    assert before[0] != before[1]
    assert ids([replace(a, page=3, bbox=(1, 2, 30, 40)), replace(a)]) == before


def test_candidate_iterator_is_consumed_once_without_skipping_records():
    candidates = [FormulaCandidate('x+y', 'tex', source_order=i) for i in range(3)]
    assert ids(iter(candidates)) == ids(candidates)


def test_pdf_occurrence_uses_full_rectangle_and_survives_cache_and_other_ocr_pages():
    a = FormulaCandidate('x+y', 'text', page=0, bbox=(10, 20, 50, 40),
                         page_w=600, page_h=800,
                         pdf_latex_chars=[(ch, (10+i*10, 20, 19+i*10, 40)) for i, ch in enumerate('x+y')])
    b = replace(a, bbox=(10, 120, 50, 140), pdf_latex_chars=None)
    index = build_index([a, b])
    assert index[0].occurrence_id != index[1].occurrence_id
    whole = search_formulas('x+y', index, mode='exact')[0]
    subpart = next(item for item in search_formulas('x', index) if item.candidate_index == 0)
    assert whole.bbox != subpart.bbox
    assert whole.occurrence_id == subpart.occurrence_id == index[0].occurrence_id
    assert ids([candidate_from_dict(candidate_to_dict(a))])[0] == index[0].occurrence_id
    ocr = replace(a, source='ocr')
    assert ids([ocr])[0] != index[0].occurrence_id
    assert ids([replace(ocr, page=4), ocr])[1] == ids([ocr])[0]


def test_formula_change_changes_identity_and_long_formulas_get_bounded_ids():
    a = FormulaCandidate('x+y', 'tex', source_order=1)
    assert ids([a]) != ids([replace(a, latex='x-y')])
    assert len(ids([replace(a, latex='x+'*20000+'y')])[0]) == 69


@pytest.mark.asyncio
async def test_search_api_returns_distinct_occurrences_stable_across_queries():
    candidates = [FormulaCandidate('x+y', 'tex', source_order=i, page=0, bbox=(10, y, 50, y+20))
                  for i, y in enumerate([20, 120])]
    url = 'https://example.org/repeated.pdf'
    main._sessions[url] = main.SessionState(index_cache=build_index(candidates), current_pdf_url=url)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        first = (await client.post('/search', json={'url': url, 'query': 'x+y', 'mode': 'exact'})).json()['matches']
        second = (await client.post('/search', json={'url': url, 'query': 'x', 'mode': 'contains'})).json()['matches']
    assert len(first) == len(second) == 2
    assert len({item['occurrence_id'] for item in first}) == 2
    assert {item['occurrence_id'] for item in first} == {item['occurrence_id'] for item in second}


@pytest.mark.parametrize('query,mode', [('x+y', 'exact'), ('x', 'contains')])
def test_source_occurrences_are_not_lost_when_mapping_collides(query, mode):
    candidates = parse_tex_formulas('First $x+y$ then $x+y$.', source_file='main.tex')
    before = search_formulas(query, build_index(candidates), mode=mode)
    mapped = [replace(c, page=0, bbox=(10, 20, 50, 40)) for c in candidates]
    after = search_formulas(query, build_index(mapped), mode=mode)
    assert len(after) == 2
    assert {r.occurrence_id for r in before} == {r.occurrence_id for r in after}
    assert all(r.bbox is None and r.bbox_source == 'ambiguous-source-location' for r in after)
    assert all(r.location_score == 0 for r in after)


def test_ambiguity_is_query_local_and_does_not_mutate_indexed_locations():
    candidates = parse_tex_formulas('First $x+y$ then $x+z$.', source_file='main.tex')
    mapped = [replace(c, page=0, bbox=(10, 20, 50, 40)) for c in candidates]
    index = build_index(mapped)
    both = search_formulas('x', index)
    assert len(both) == 2 and all(r.bbox is None for r in both)
    unique = search_formulas('x+y', index, mode='exact')
    assert len(unique) == 1 and unique[0].bbox == (10, 20, 50, 40)
    assert all(c.bbox == (10, 20, 50, 40) for c in index)


def test_duplicate_delivery_of_one_source_identity_does_not_create_ambiguity():
    candidate = build_index([FormulaCandidate('x+y', 'tex', page=0, bbox=(10, 20, 50, 40))])[0]
    result = search_formulas('x+y', [candidate, candidate], mode='exact')
    assert len(result) == 1 and result[0].bbox == (10, 20, 50, 40)


def test_non_source_duplicate_rectangles_keep_the_existing_deduplication():
    candidate = FormulaCandidate('x+y', 'text', page=0, bbox=(10, 20, 50, 40))
    result = search_formulas('x+y', build_index([candidate, replace(candidate)]), mode='exact')
    assert len(result) == 1 and result[0].bbox == (10, 20, 50, 40)


@pytest.mark.asyncio
async def test_search_api_labels_collisions_and_preserves_both_source_anchors():
    candidates = parse_tex_formulas('First $x+y$.\nThen $x+y$.', source_file='main.tex')
    candidates = [replace(c, page=0, bbox=(10, 20, 50, 40)) for c in candidates]
    url = 'https://example.org/collision.pdf'
    main._sessions[url] = main.SessionState(index_cache=build_index(candidates), current_pdf_url=url)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        results = (await client.post('/search', json={'url': url, 'query': 'x+y', 'mode': 'exact'})).json()['matches']
    assert len(results) == 2
    assert {r['source_line'] for r in results} == {1, 2}
    assert all(r['location_status'] == 'ambiguous' and r['bbox'] is None for r in results)
