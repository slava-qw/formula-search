import pytest
import fitz
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from backend.extractor.arxiv_tex import ArxivDocument


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def app():
    from backend.main import app
    return app


@pytest.mark.asyncio
async def test_process_returns_formula_count(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/process",
            files={"file": ("doc.pdf", pdf, "application/pdf")},
        )
    assert response.status_code == 200
    data = response.json()
    assert "formula_count" in data
    assert "source_type" in data
    assert isinstance(data["formula_count"], int)
    assert data["source_type"] == "text"


@pytest.mark.asyncio
async def test_process_plain_pdf_returns_zero_formulas(app):
    pdf = _make_pdf("The quick brown fox.")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/process",
            files={"file": ("doc.pdf", pdf, "application/pdf")},
        )
    assert response.status_code == 200
    assert response.json()["formula_count"] == 0


@pytest.mark.asyncio
async def test_search_returns_matches_after_process(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/search", json={"query": r"\frac{d}{dx}", "url": ""})
    assert response.status_code == 200
    data = response.json()
    assert "matches" in data
    assert isinstance(data["matches"], list)
    assert len(data["matches"]) >= 1


@pytest.mark.asyncio
async def test_search_match_has_required_fields(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/search", json={"query": r"\frac{d}{dx}", "url": ""})
    matches = response.json()["matches"]
    assert len(matches) >= 1, "Expected at least one match for exact formula query"
    m = matches[0]
    assert "score" in m
    assert "match_type" in m
    assert "latex" in m
    assert "label" in m
    assert "section" in m
    assert "source" in m
    assert m["match_type"] in ("exact", "partial", "fuzzy")


@pytest.mark.asyncio
async def test_search_no_match_returns_empty_list(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/search", json={"query": r"\Omega_{z}^{42}", "url": ""})
    assert response.json()["matches"] == []


@pytest.mark.asyncio
async def test_search_modes_and_location_contract(app):
    import backend.main as main
    from backend.extractor.text_layer import FormulaCandidate
    from backend.matcher.index import build_index
    url = "https://example.com/modes.pdf"
    main._sessions[url] = main.SessionState(index_cache=build_index([
        FormulaCandidate("x+y", "tex"), FormulaCandidate("x+y+z", "tex", page=2),
    ]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        contains = (await client.post("/search", json={"query": "x+y", "url": url})).json()
        exact = (await client.post("/search", json={"query": "x+y", "url": url, "mode": "exact"})).json()
        invalid = await client.post("/search", json={"query": "x+y", "url": url, "mode": "invalid"})
        empty = await client.post("/search", json={"query": "", "url": url})
    assert contains["mode"] == "contains" and contains["search_ms"] >= 0
    assert len(contains["matches"]) == 2
    assert {m["location_status"] for m in contains["matches"]} == {"unlocated", "page-only"}
    assert [m["latex"] for m in exact["matches"]] == ["x+y"]
    assert invalid.status_code == empty.status_code == 422


@pytest.mark.asyncio
async def test_ocr_page_without_process_returns_400(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/ocr-page", json={"page": 0, "url": ""})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_ocr_page_out_of_range_returns_400(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/ocr-page", json={"page": 999, "url": ""})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_ocr_page_returns_formulas_found(app):
    from backend.extractor.text_layer import FormulaCandidate
    pdf = _make_pdf(r"\frac{d}{dx}")
    mock_result = [
        FormulaCandidate(latex=r"\frac{d}{dx}", source="ocr", page=0)
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        with patch("backend.main.run_ocr", return_value=mock_result):
            response = await client.post("/ocr-page", json={"page": 0, "url": ""})
    assert response.status_code == 200
    data = response.json()
    assert "formulas_found" in data
    assert data["formulas_found"] == 1


@pytest.mark.asyncio
async def test_ocr_page_merges_into_cache_preserving_text_layer(app):
    from backend.extractor.text_layer import FormulaCandidate
    from backend.matcher.index import build_index
    import backend.main
    pdf = _make_pdf(r"\frac{d}{dx}")
    text_candidate = FormulaCandidate(latex=r"\frac{d}{dx}", source="text", page=0)
    old_ocr = FormulaCandidate(latex=r"\alpha", source="ocr", page=0)
    new_ocr = FormulaCandidate(latex=r"\int x\,dx", source="ocr", page=0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        backend.main._sessions[""].index_cache.extend(build_index([text_candidate, old_ocr]))
        with patch("backend.main.run_ocr", return_value=[new_ocr]):
            await client.post("/ocr-page", json={"page": 0, "url": ""})
    latexes = [c.latex for c in backend.main._sessions[""].index_cache]
    sources = [c.source for c in backend.main._sessions[""].index_cache]
    # text candidate preserved
    assert r"\frac{d}{dx}" in latexes
    assert "text" in sources
    # new OCR candidate present
    assert r"\int x\,dx" in latexes
    assert "ocr" in sources
    # old OCR candidate evicted
    assert r"\alpha" not in latexes


@pytest.mark.asyncio
async def test_ocr_page_negative_page_returns_400(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/ocr-page", json={"page": -1, "url": ""})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_status_before_process_returns_not_loaded(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["loaded"] is False
    assert data["formula_count"] == 0
    assert data["url"] == ""


@pytest.mark.asyncio
async def test_status_after_process_returns_loaded(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/process",
            files={"file": ("doc.pdf", pdf, "application/pdf")},
            data={"url": "https://example.com/paper.pdf"},
        )
        response = await client.get("/status", params={"url": "https://example.com/paper.pdf"})
    assert response.status_code == 200
    data = response.json()
    assert data["loaded"] is True
    assert isinstance(data["formula_count"], int)
    assert data["url"] == "https://example.com/paper.pdf"


@pytest.mark.asyncio
async def test_process_arxiv_url_returns_source_type_tex(app):
    from backend.extractor.text_layer import FormulaCandidate

    async def _fake_fetch(arxiv_id, **kwargs):
        return ArxivDocument([FormulaCandidate(latex=r"E = mc^2", source="tex", label="eq:e", section="Intro")])

    with patch("backend.main.fetch_arxiv_document", side_effect=_fake_fetch):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/process",
                data={"url": "https://arxiv.org/pdf/1706.03762v7"},
            )
    assert response.status_code == 200
    data = response.json()
    assert data["formula_count"] == 1
    assert data["source_type"] == "tex"


@pytest.mark.asyncio
async def test_process_non_arxiv_pdf_returns_source_type_text(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/process",
            files={"file": ("doc.pdf", pdf, "application/pdf")},
        )
    assert response.status_code == 200
    assert response.json()["source_type"] == "text"


@pytest.mark.asyncio
async def test_process_no_url_no_file_returns_400(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/process", data={})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_search_match_highlighted_latex_field_present(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/search", json={"query": r"\frac{d}{dx}", "url": ""})
    matches = response.json()["matches"]
    assert len(matches) >= 1
    assert "highlighted_latex" in matches[0]
    assert isinstance(matches[0]["highlighted_latex"], str)


@pytest.mark.asyncio
async def test_search_response_includes_page_and_bbox(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/search", json={"query": r"\frac{d}{dx}", "url": ""})
    matches = response.json()["matches"]
    assert len(matches) >= 1
    m = matches[0]
    assert "page" in m
    assert isinstance(m["page"], int)
    assert "bbox" in m
    assert m["bbox"] is None or (isinstance(m["bbox"], list) and len(m["bbox"]) == 4)


@pytest.mark.asyncio
async def test_search_response_includes_page_w_h(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("doc.pdf", pdf, "application/pdf")})
        response = await client.post("/search", json={"query": r"\frac{d}{dx}", "url": ""})
    matches = response.json()["matches"]
    assert len(matches) >= 1
    m = matches[0]
    assert "page_w" in m
    assert "page_h" in m
    assert isinstance(m["page_w"], float)
    assert isinstance(m["page_h"], float)
    assert m["page_w"] > 0
    assert m["page_h"] > 0


@pytest.mark.asyncio
async def test_status_includes_source_type_and_pdf_cache_flag(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/process",
            files={"file": ("doc.pdf", pdf, "application/pdf")},
            data={"url": "https://example.com/paper.pdf"},
        )
        response = await client.get("/status", params={"url": "https://example.com/paper.pdf"})
    data = response.json()
    assert data["source_type"] == "text"
    assert data["pdf_cached"] is True


@pytest.mark.asyncio
async def test_arxiv_process_does_not_eagerly_map_bboxes(app):
    """/process on the arXiv path should only build the fast LineIndex, not
    map every candidate's PDF location up front -- that's deferred to /search
    (see test_search_lazily_resolves_bbox_for_matched_candidate_only)."""
    import backend.main
    from backend.extractor.arxiv_tex import build_line_index
    from backend.extractor.text_layer import FormulaCandidate

    pdf = _make_pdf(r"E = mc^2")
    candidates = [
        FormulaCandidate(latex=r"E = mc^2", source="tex", label="eq:e", section="Intro"),
        FormulaCandidate(latex=r"F = ma", source="tex", label="eq:f", section="Intro"),
    ]
    line_index = build_line_index(pdf, candidates)

    async def _fake_fetch(arxiv_id, **kwargs):
        return ArxivDocument(candidates, line_index, pdf, 1)

    with patch("backend.main.fetch_arxiv_document", side_effect=_fake_fetch):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/process", data={"url": "https://arxiv.org/pdf/1706.03762v7"})

    assert response.json()["formula_count"] == 2
    assert all(c.page == -1 and c.bbox is None for c in backend.main._sessions["https://arxiv.org/pdf/1706.03762v7"].candidates_cache)
    assert all(m.page == -1 and m.bbox is None for m in backend.main._sessions["https://arxiv.org/pdf/1706.03762v7"].index_cache)


@pytest.mark.asyncio
async def test_search_lazily_resolves_bbox_for_matched_candidate_only(app):
    import backend.main
    from backend.extractor.arxiv_tex import build_line_index
    from backend.extractor.text_layer import FormulaCandidate

    pdf = _make_pdf(r"E = mc^2")
    candidates = [
        FormulaCandidate(latex=r"E = mc^2", source="tex", label="eq:e", section="Intro"),
        FormulaCandidate(latex=r"F = ma", source="tex", label="eq:f", section="Intro"),
    ]
    line_index = build_line_index(pdf, candidates)

    async def _fake_fetch(arxiv_id, **kwargs):
        return ArxivDocument(candidates, line_index, pdf, 1)

    with patch("backend.main.fetch_arxiv_document", side_effect=_fake_fetch):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/process", data={"url": "https://arxiv.org/pdf/1706.03762v7"})
            search_response = await client.post("/search", json={"query": r"E = mc^2", "url": "https://arxiv.org/pdf/1706.03762v7"})

    matches = search_response.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["page"] == 0
    assert matches[0]["bbox"] is not None

    # Only the matched candidate ("E = mc^2") was resolved; the other
    # ("F = ma") is untouched, proving this maps on demand rather than
    # falling back to eagerly mapping the whole document on first search.
    resolved = [c for c in backend.main._sessions["https://arxiv.org/pdf/1706.03762v7"].candidates_cache if c.bbox is not None]
    assert len(resolved) == 1
    assert resolved[0].latex == r"E = mc^2"

    # A repeat search for the same formula must find it already resolved
    # (idempotent -- not remapped every time it's searched).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second_response = await client.post("/search", json={"query": r"E = mc^2", "url": "https://arxiv.org/pdf/1706.03762v7"})
    assert second_response.json()["matches"][0]["bbox"] == matches[0]["bbox"]


@pytest.mark.asyncio
async def test_pdf_upload_takes_precedence_over_arxiv_url_for_viewer_cache(app):
    pdf = _make_pdf(r"\frac{d}{dx}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/process",
            files={"file": ("doc.pdf", pdf, "application/pdf")},
            data={"url": "https://arxiv.org/pdf/1706.03762v7"},
        )
    assert response.status_code == 200
    assert response.json()["source_type"] == "text"
    assert response.json()["pdf_cached"] is True


@pytest.mark.asyncio
async def test_late_pdf_upload_preserves_tex_index_for_identical_pdf(app):
    from backend.extractor.text_layer import FormulaCandidate

    async def _fake_fetch(arxiv_id, **kwargs):
        return ArxivDocument([FormulaCandidate(latex=r"E = mc^2", source="tex", label="eq:e", section="Intro")], pdf_bytes=pdf, page_count=1)

    pdf = _make_pdf(r"E = mc^2")
    url = "https://arxiv.org/pdf/1706.03762v7"
    with patch("backend.main.fetch_arxiv_document", side_effect=_fake_fetch):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            tex_response = await client.post("/process", data={"url": url})
            upload_response = await client.post(
                "/process",
                files={"file": ("doc.pdf", pdf, "application/pdf")},
                data={"url": url},
            )
            search_response = await client.post("/search", json={"query": r"E = mc^2", "url": url})
    assert tex_response.json()["source_type"] == "tex"
    assert upload_response.json()["source_type"] == "tex"
    assert search_response.json()["matches"][0]["source"] == "tex"


@pytest.mark.asyncio
async def test_multi_session_search_isolation(app):
    """Two different documents processed in the same backend process must
    not leak formulas into each other's /search results."""
    pdf_a = _make_pdf(r"\alpha_{one}")
    pdf_b = _make_pdf(r"\beta_{two}")
    url_a = "https://example.com/paper-a.pdf"
    url_b = "https://example.com/paper-b.pdf"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("a.pdf", pdf_a, "application/pdf")}, data={"url": url_a})
        await client.post("/process", files={"file": ("b.pdf", pdf_b, "application/pdf")}, data={"url": url_b})

        response_a = await client.post("/search", json={"query": r"\alpha_{one}", "url": url_a})
        response_b = await client.post("/search", json={"query": r"\beta_{two}", "url": url_b})
        cross_a = await client.post("/search", json={"query": r"\beta_{two}", "url": url_a})
        cross_b = await client.post("/search", json={"query": r"\alpha_{one}", "url": url_b})

    assert len(response_a.json()["matches"]) >= 1
    assert len(response_b.json()["matches"]) >= 1
    assert cross_a.json()["matches"] == []
    assert cross_b.json()["matches"] == []


@pytest.mark.asyncio
async def test_multi_session_ocr_page_isolation(app):
    """OCR results merged into one document's session must not appear in a
    different document's session, even though ocr_page() itself is mocked
    identically for both calls."""
    from backend.extractor.text_layer import FormulaCandidate
    pdf_a = _make_pdf(r"\alpha_{one}")
    pdf_b = _make_pdf(r"\beta_{two}")
    url_a = "https://example.com/paper-a.pdf"
    url_b = "https://example.com/paper-b.pdf"
    ocr_result = [FormulaCandidate(latex=r"\gamma_{ocr}", source="ocr", page=0)]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("a.pdf", pdf_a, "application/pdf")}, data={"url": url_a})
        await client.post("/process", files={"file": ("b.pdf", pdf_b, "application/pdf")}, data={"url": url_b})

        with patch("backend.main.run_ocr", return_value=ocr_result):
            ocr_response = await client.post("/ocr-page", json={"page": 0, "url": url_a})
        assert ocr_response.status_code == 200

        response_a = await client.post("/search", json={"query": r"\gamma_{ocr}", "url": url_a})
        response_b = await client.post("/search", json={"query": r"\gamma_{ocr}", "url": url_b})

    assert len(response_a.json()["matches"]) == 1
    assert response_b.json()["matches"] == []


@pytest.mark.asyncio
async def test_unknown_session_search_returns_empty_matches(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/search", json={"query": r"\alpha", "url": "https://example.com/never-processed.pdf"}
        )
    assert response.status_code == 200
    assert response.json()["matches"] == []


@pytest.mark.asyncio
async def test_unknown_session_ocr_page_returns_400(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/ocr-page", json={"page": 0, "url": "https://example.com/never-processed.pdf"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_status_reports_per_session(app):
    pdf_a = _make_pdf(r"\alpha_{one}")
    url_a = "https://example.com/paper-a.pdf"
    url_b = "https://example.com/paper-b.pdf"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/process", files={"file": ("a.pdf", pdf_a, "application/pdf")}, data={"url": url_a})
        status_a = await client.get("/status", params={"url": url_a})
        status_b = await client.get("/status", params={"url": url_b})
    assert status_a.json()["loaded"] is True
    assert status_a.json()["url"] == url_a
    assert status_b.json()["loaded"] is False
    assert status_b.json()["url"] == ""


@pytest.mark.asyncio
async def test_status_without_url_param_defaults_to_empty_session(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["loaded"] is False
    assert data["url"] == ""
