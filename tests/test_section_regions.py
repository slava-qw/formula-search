"""Rendered contents entries must not bound formula searches to the contents."""
import fitz
import hashlib
import json
import os
from pathlib import Path
import pytest

from backend.extractor import arxiv_tex as tex


def heading(page, text, y, number='1', page_ref=None):
    page.insert_text((50, y), number, fontname='hebo', fontsize=12)
    page.insert_text((85, y), text, fontname='hebo', fontsize=12)
    if page_ref is not None:
        page.insert_text((520, y), str(page_ref), fontname='hebo', fontsize=12)


def regions(doc, names):
    return tex._find_section_regions(tex._extract_pdf_lines(doc), set(names))


def test_contents_reference_is_not_a_section_start_or_search_region():
    with fitz.open() as doc:
        toc = doc.new_page()
        toc.insert_text((50, 50), 'Contents', fontsize=16)
        heading(toc, 'Methods', 100, page_ref=2)
        page = doc.new_page()
        heading(page, 'Methods', 100)
        page.insert_text((85, 150), 'x + y = z', fontsize=12)
        assert regions(doc, ['methods'])['methods'][0][0] == 1
        candidates = tex.parse_tex_formulas(r'\section{Methods}\[x+y=z\]')
        tex.assign_bboxes_from_pdf(candidates, doc.tobytes())
        assert candidates[0].page == 1 and candidates[0].bbox is not None


def test_unlabelled_contents_continuation_uses_aligned_page_references():
    with fitz.open() as doc:
        toc = doc.new_page()
        for i, name in enumerate(['Methods', 'Experiments', 'Conclusion']):
            heading(toc, name, 100+i*30, number=str(i+1), page_ref=i+2)
        for i, name in enumerate(['Methods', 'Experiments', 'Conclusion']):
            page = doc.new_page()
            heading(page, name, 100, number=str(i+1))
        found = regions(doc, ['methods', 'experiments', 'conclusion'])
        assert [found[key][0][0] for key in ['methods', 'experiments', 'conclusion']] == [1, 2, 3]


def test_contents_without_a_body_heading_does_not_invent_a_region():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), 'Table of contents', fontsize=16)
        heading(page, 'Unrendered appendix', 100, page_ref=8)
        assert regions(doc, ['unrendered appendix']) == {}


def test_real_section_can_start_below_contents_on_the_same_page():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), 'Contents', fontsize=16)
        heading(page, 'Methods', 100, page_ref=1)
        heading(page, 'Methods', 300)
        start = regions(doc, ['methods'])['methods'][0]
        assert start[0] == 0 and start[1] > 280


def test_numbered_body_headings_and_plain_synthetic_headings_remain_eligible():
    with fitz.open() as doc:
        page = doc.new_page()
        heading(page, 'Methods', 100)
        heading(page, 'Experiments', 200, number='2')
        page.insert_text((85, 300), 'Conclusion', fontsize=12)
        found = regions(doc, ['methods', 'experiments', 'conclusion'])
        assert set(found) == {'methods', 'experiments', 'conclusion'}
        assert found['methods'][1] == found['experiments'][0]
        assert found['experiments'][1] == found['conclusion'][0]


def test_isolated_right_number_without_contents_evidence_is_not_enough_to_reject():
    with fitz.open() as doc:
        page = doc.new_page()
        heading(page, 'Methods', 100, page_ref=2)
        assert 'methods' in regions(doc, ['methods'])


ROOT = Path(__file__).resolve().parents[1]
PROBES = json.loads((ROOT/'testdata/benchmarks/source_section_probes.json').read_text())['papers']


@pytest.mark.skipif(os.environ.get('RUN_PAPER_AUDITS') != '1', reason='set RUN_PAPER_AUDITS=1 for real PDF section gates')
@pytest.mark.parametrize('probe', PROBES, ids=lambda p: p['paper'])
def test_reviewed_real_section_starts_are_body_headings(probe):
    manifest = json.loads((ROOT/'testdata/benchmarks/manifest.json').read_text())['papers'][probe['paper']]
    path = ROOT/manifest['artifacts']['pdf']['path']
    if not path.exists():
        pytest.skip('Pinned PDF fixture is not downloaded')
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == probe['pdf_sha256']
    with fitz.open(stream=data, filetype='pdf') as doc:
        found = regions(doc, [item['key'] for item in probe['headings']])
    for expected in probe['headings']:
        page, y = found[expected['key']][0]
        assert page == expected['page'], expected['key']
        assert abs(y-expected['y']) < 1, expected['key']
