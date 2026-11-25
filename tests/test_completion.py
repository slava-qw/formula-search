"""Completion failures: stuck work, unsafe geometry and durable state."""
import asyncio
from dataclasses import replace
import os
import time

import fitz
import httpx
import pytest

from backend import cache, main, processing
from backend.extractor.ocr_page import ocr_page, page_preview
from backend.extractor.ocr_page import formula_preview
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas


@pytest.mark.asyncio
async def test_deadline_recovers_capacity_even_after_caller_cancels(monkeypatch):
    processing.shutdown_processing()
    monkeypatch.setattr(processing, 'DOCUMENT_TIMEOUT_SECONDS', .5)
    task = asyncio.create_task(processing.run_document_job(time.sleep, 60))
    await asyncio.sleep(.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with asyncio.timeout(8):
        while processing._pending:
            await asyncio.sleep(.02)
    monkeypatch.setattr(processing, 'DOCUMENT_TIMEOUT_SECONDS', 10)
    assert await processing.run_document_job(os.getpid) != os.getpid()


@pytest.mark.asyncio
async def test_deadline_is_retryable_and_next_job_succeeds(monkeypatch):
    processing.shutdown_processing()
    monkeypatch.setattr(processing, 'DOCUMENT_TIMEOUT_SECONDS', .5)
    with pytest.raises(processing.ProcessingTimeout):
        await processing.run_document_job(time.sleep, 60)
    monkeypatch.setattr(processing, 'DOCUMENT_TIMEOUT_SECONDS', 10)
    assert await processing.run_document_job(os.getpid) != os.getpid()


def test_cache_prunes_owned_namespace_only_and_bounds_decoding(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_CACHE_ENTRIES', 2)
    legacy = cache.CACHE_DIR / 'user-file.json.gz'
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b'keep')
    for number in range(3):
        cache.store_candidates_cache('unit', str(number), [FormulaCandidate('x+y', 'tex')])
    assert len(list((cache.CACHE_DIR/'candidates-v1').glob('*.json.gz'))) == 2
    assert legacy.read_bytes() == b'keep'
    monkeypatch.setattr(cache, 'MAX_DECODED_BYTES', 2)
    assert cache.load_candidates_cache('unit', '2') is None


def test_source_anchor_survives_insertion_elsewhere_but_distinguishes_repeated_site():
    c = FormulaCandidate('x+y', 'tex', source_file='main.tex', source_line=12, source_order=3)
    before, = build_index([c])
    shifted, = build_index([replace(c, source_order=47)])
    assert before.occurrence_id != shifted.occurrence_id
    assert before.occurrence_anchor == shifted.occurrence_anchor
    repeated = build_index([c, replace(c, source_order=48)])
    assert repeated[0].occurrence_anchor != repeated[1].occurrence_anchor


def test_unicode_highlight_requires_complete_row_and_script_geometry():
    chars = [('\U0001d454',(10,10,20,30)), ('\U0001d461',(20,18,26,33)),
             ('=',(30,10,40,30)), ('x',(45,10,55,30))]
    candidate = FormulaCandidate('g_t=x', 'tex', page=0, bbox=(10,10,55,33), page_w=100, page_h=100,
                                 pdf_line_text=''.join(ch for ch,_ in chars), pdf_line_raw_chars=chars)
    result, = search_formulas('g_t',build_index([candidate]))
    assert result.bbox == (10,10,26,33)
    # Recognizing g_t on an unrelated equation is insufficient.
    result, = search_formulas('g_t',build_index([replace(candidate,latex='g_t=y+1')]))
    assert result.bbox is None
    result = search_formulas('g^t',build_index([candidate]))
    assert result == []


def test_persisted_mapping_restores_complete_geometry_and_not_another_pdf(monkeypatch):
    candidate = FormulaCandidate('x+y', 'tex', source_file='main.tex', source_line=1,
                                 source_order=0, page=2, bbox=(10, 20, 40, 50), bbox_quality='pdf-line')
    session = main.SessionState(mapping_key='mapping-v1:paper:pdf-sha', mapping_revision=1,
                                candidates_cache=[candidate])
    main._persist_locations(session)
    saved, = cache.load_candidates_cache('source-locations', session.mapping_key)
    assert saved.bbox == candidate.bbox and saved.source_file == candidate.source_file
    assert cache.load_candidates_cache('source-locations', 'mapping-v1:paper:other-pdf') is None
    assert session.persisted_mapping_revision == session.mapping_revision


def paper_pdf(rotation=0):
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 60), 'x+y=3')
        page.insert_text((40, 160), 'a+b=7')
        page.set_rotation(rotation)
        return doc.tobytes()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_crop_is_bounded_and_returns_display_coordinates(monkeypatch, rotation):
    import backend.extractor.ocr_page as module
    images = []
    monkeypatch.setattr(module, '_ocr_model', lambda image: images.append(image.size) or 'x+y=3')
    pdf = paper_pdf(rotation)
    preview = page_preview(pdf, 0)
    assert preview['image'].startswith('data:image/png;base64,')
    region = (10, 20, 100, 80)
    result, = ocr_page(pdf, 0, region)
    assert result.bbox == region
    assert result.page_w == preview['page_w'] and result.page_h == preview['page_h']
    assert images == [(180, 120)]
    with pytest.raises(ValueError, match='single equation'):
        ocr_page(pdf, 0, (0, 0, preview['page_w'], preview['page_h']))
    with pytest.raises(ValueError, match='inside'):
        ocr_page(pdf, 0, (-1, 0, 100, 80))


def test_pdf_excerpt_retains_actual_pixels_and_outlines_selected_geometry():
    import base64
    import io
    from PIL import Image
    preview = formula_preview(paper_pdf(),0,(35,45,100,65))
    image = Image.open(io.BytesIO(base64.b64decode(preview['image'].split(',',1)[1])))
    assert image.width > 130 and image.height > 40
    assert any(color == (37,99,235) for color in image.getdata())
    assert any(max(color) < 100 for color in image.getdata())


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_rotated_crop_contains_the_actual_equation_glyphs(rotation):
    import base64
    import io
    from PIL import Image
    from backend.extractor.ocr_page import equation_crop
    from backend.pdf_geometry import displayed_geometry
    box,_,_ = displayed_geometry((35,45,105,65),300,400,rotation)
    crop = equation_crop(paper_pdf(rotation),0,box)
    image = Image.open(io.BytesIO(base64.b64decode(crop['image'])))
    assert sum(max(pixel)<100 for pixel in image.getdata()) > 20


@pytest.mark.asyncio
async def test_ocr_regions_survive_independent_recognition_and_reject_stale_preview(monkeypatch):
    url = 'file:///regions.pdf'
    session = main.SessionState(pdf_bytes=paper_pdf(), current_pdf_url=url)
    main._sessions[url] = session
    async def recognize(pdf, page, region):
        return [FormulaCandidate('x+y', 'ocr', page=page, bbox=region, page_w=300, page_h=400)]
    monkeypatch.setattr(main, 'run_ocr', recognize)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        for region in [(10,20,100,80), (10,120,100,180), (10,20,100,80)]:
            response = await client.post('/ocr-page', json={'url':url, 'page':0, 'region':region,
                                                          'context_revision':session.context_revision})
            assert response.status_code == 200, response.text
        assert len(session.index_cache) == 2
        stale = await client.post('/ocr-page', json={'url':url, 'page':0, 'region':region, 'context_revision':'old'})
        assert stale.status_code == 409


@pytest.mark.asyncio
async def test_health_and_source_only_status_are_explicit():
    main._sessions['paper'] = main.SessionState(source_type='tex', pdf_warning='PDF unavailable')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        health = (await client.get('/health')).json()
        assert health['capabilities']['worker_deadlines']
        status = (await client.get('/status', params={'url':'paper'})).json()
        assert status['loaded'] and status['pdf_warning'] and not status['pdf_cached']


@pytest.mark.asyncio
async def test_remote_websites_cannot_read_papers_or_submit_processing_jobs(monkeypatch):
    async def forbidden(*args,**kwargs):
        pytest.fail('Untrusted browser request reached document processing')
    monkeypatch.setattr(main,'_prepare_source',forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
        for origin in ['https://example.com','null','http://localhost.evil.example']:
            response = await client.post('/process',data={'url':'https://arxiv.org/pdf/2312.00752v2'},headers={'Origin':origin})
            assert response.status_code == 403
            assert (await client.get('/status',headers={'Origin':origin})).status_code == 403
        for origin in ['chrome-extension://'+'a'*32,'http://127.0.0.1:7878','http://localhost:9999']:
            response = await client.get('/health',headers={'Origin':origin})
            assert response.status_code == 200 and response.headers['access-control-allow-origin'] == origin


@pytest.mark.asyncio
async def test_recognition_requires_review_before_indexing_and_uses_server_geometry(monkeypatch):
    url = 'file:///review.pdf'
    session = main.SessionState(pdf_bytes=paper_pdf(),current_pdf_url=url)
    main._sessions[url] = session
    async def recognize(pdf,page,region):
        return [FormulaCandidate(r'\mathcal{N}=16','ocr',page=page,bbox=region,page_w=300,page_h=400)]
    monkeypatch.setattr(main,'run_ocr',recognize)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
        response = await client.post('/ocr-page',json={'url':url,'page':0,'region':[10,20,100,80],
                                                      'context_revision':session.context_revision,'preview_only':True})
        proposal = response.json()
        assert response.status_code == 200 and proposal['requires_review']
        assert session.index_cache == []
        saved = await client.post('/ocr-save',json={'url':url,'proposal_id':proposal['proposal_id'],'latex':'N=16',
                                                   'page':100,'bbox':[1,2,3,4]})
        assert saved.status_code == 200
        candidate, = session.index_cache
        assert candidate.latex == 'N=16' and candidate.bbox == (10,20,100,80) and candidate.page == 0
        assert (await client.post('/ocr-save',json={'url':url,'proposal_id':proposal['proposal_id'],'latex':'N=16'})).status_code == 409
