"""Source excerpts belong to one prepared document and exact occurrence."""
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main
from backend.cache import candidate_from_dict, candidate_to_dict
from backend.extractor import arxiv_tex as tex
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.index import build_index


URL = 'https://arxiv.org/pdf/1706.03762v7'


def publish(candidates, url=URL, **kwargs):
    state = main.SessionState(index_cache=build_index(candidates), candidates_cache=candidates,
                             source_type='tex', current_pdf_url=url, generation=1, **kwargs)
    main._sessions[url] = state
    return state


def request(state, number=0, **kwargs):
    return dict(url=state.current_pdf_url, occurrence_id=state.index_cache[number].occurrence_id,
                context_revision=state.context_revision, **kwargs)


@pytest.mark.asyncio
async def test_repeated_source_occurrences_get_their_own_excerpts_without_pdf_mapping(monkeypatch):
    candidates = tex.parse_tex_formulas('First estimate $x$ is small.\nSecond estimate $x$ is large.')
    state = publish(candidates)
    monkeypatch.setattr(main, 'resolve_candidate_bbox', lambda *args: pytest.fail('Context must not map the PDF'))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        result = (await client.post('/search', json={'url': URL, 'query': 'x'})).json()
        assert result['source_context_revision'] == state.context_revision
        assert len(result['matches']) == 2
        for number, match in enumerate(result['matches']):
            response = await client.post('/source-context', json=request(state, number))
            assert response.status_code == 200
            data = response.json()
            assert data == {'kind': 'tex', 'occurrence_id': match['occurrence_id'],
                            'context_revision': state.context_revision,
                            'before': candidates[number].tex_context_before,
                            'after': candidates[number].tex_context_after}
    assert candidates[0].tex_context_before != candidates[1].tex_context_before
    assert all(c.page == -1 and c.bbox is None for c in candidates)


@pytest.mark.asyncio
async def test_replacement_with_same_formula_id_rejects_old_context_revision():
    original = publish(tex.parse_tex_formulas('Original words $x$ continue here.'))
    replacement = publish([replace(original.candidates_cache[0], tex_context_before='Changed words ')])
    assert original.index_cache[0].occurrence_id == replacement.index_cache[0].occurrence_id
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        old = await client.post('/source-context', json=request(original))
        new = await client.post('/source-context', json=request(replacement))
    assert old.status_code == 409
    assert new.status_code == 200 and new.json()['before'] == 'Changed words '


@pytest.mark.asyncio
async def test_document_isolation_and_unknown_url_do_not_create_sessions():
    first = publish(tex.parse_tex_formulas('First $x$ text.'))
    second_url = 'https://arxiv.org/pdf/1706.03762v6'
    publish(tex.parse_tex_formulas('Other $x$ text.'), url=second_url)
    missing_url = 'https://example.org/missing.pdf'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        for url in [second_url, missing_url]:
            response = await client.post('/source-context', json={**request(first), 'url': url})
            assert response.status_code == 409
    assert missing_url not in main._sessions


@pytest.mark.asyncio
async def test_cache_reload_preserves_excerpt_and_source_identity():
    candidates = tex.parse_tex_formulas('Before $x+y$ after.')
    original_id = build_index(candidates)[0].occurrence_id
    state = publish([candidate_from_dict(candidate_to_dict(c)) for c in candidates])
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/source-context', json=request(state))
    assert response.status_code == 200
    assert response.json()['occurrence_id'] == original_id
    assert response.json()['before'] == candidates[0].tex_context_before


@pytest.mark.asyncio
async def test_ocr_append_does_not_move_source_excerpt_and_non_source_records_are_rejected():
    state = publish(tex.parse_tex_formulas('Before $x$ after.'))
    state.index_cache += build_index([FormulaCandidate('x', 'ocr', page=0)])
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        source = await client.post('/source-context', json=request(state))
        ocr = await client.post('/source-context', json=request(state, 1))
        unknown = await client.post('/source-context', json={**request(state), 'occurrence_id': 'occ1:'+'f'*64})
    assert source.status_code == 200
    assert ocr.status_code == unknown.status_code == 404


@pytest.mark.asyncio
async def test_inconsistent_candidate_order_is_rejected():
    state = publish(tex.parse_tex_formulas('First $x$ then $y$ last.'))
    state.candidates_cache = list(reversed(state.candidates_cache))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/source-context', json=request(state))
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_large_or_html_like_source_is_bounded_literal_text():
    state = publish([FormulaCandidate('x', 'tex', tex_context_before='a'*10000+'<script>x</script>',
                                     tex_context_after='<img src=x onerror=alert(1)>'+'b'*10000)])
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/source-context', json=request(state))
    data = response.json()
    assert response.status_code == 200
    assert len(data['before']) == len(data['after']) == 420
    assert data['before'].endswith('<script>x</script>')
    assert data['after'].startswith('<img src=x onerror=alert(1)>')


@pytest.mark.asyncio
async def test_pdf_only_search_does_not_advertise_source_context():
    state = publish([FormulaCandidate('x', 'text', page=0)])
    state.source_type = 'text'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/search', json={'url': URL, 'query': 'x'})
    assert response.json()['source_context_revision'] == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('context_revision', ''), ('occurrence_id', 'x')])
async def test_invalid_context_identifiers_are_rejected(field, value):
    state = publish(tex.parse_tex_formulas('$x$'))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/source-context', json={**request(state), field: value})
    assert response.status_code == 422
