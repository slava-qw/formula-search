"""Embedded font evidence, Unicode errors and mathematical font distinctions."""
from functools import lru_cache
from pathlib import Path

import fitz
import pytest

from tests.test_pdf_math import _search
from tools.audit_formula_mapping import DEFAULT_PAPERS


@lru_cache(maxsize=8)
def _embedded(paper, name):
    if paper == 'mamba':
        path = Path(__file__).resolve().parents[1]/'testdata/benchmarks/artifacts/2312.00752v2.pdf'
        if not path.exists():
            pytest.skip('Fetch pinned Mamba with tools/fetch_paper_fixtures.py')
    else:
        path = DEFAULT_PAPERS[paper]['pdf']
    if not path.exists():
        pytest.skip('Restore external fixtures with python tools/fetch_paper_fixtures.py')
    with fitz.open(path) as doc:
        for page in doc:
            for xref, _, _, font_name, *_ in page.get_fonts():
                if font_name.split('+')[-1] == name:
                    return doc.extract_font(xref)[3]
    raise AssertionError((paper, name))


def _symbol_pdf(paper, font_name, symbol, corrupt=None, suffix=''):
    with fitz.open() as doc:
        page = doc.new_page()
        xref = page.insert_font(fontname='MATH', fontbuffer=_embedded(paper, font_name))
        # Use the font's PostScript name for the resource, as the real papers do.
        # MuPDF's insertion otherwise uses the generic Type1 family display name.
        doc.xref_set_key(xref, 'BaseFont', '/'+font_name)
        page.insert_text((50, 100), symbol, fontname='MATH', fontsize=12)
        if suffix:
            page.insert_text((65, 100), suffix, fontsize=12)
        if corrupt:
            cmap_ref = int(doc.xref_get_key(xref, 'ToUnicode')[1].split()[0])
            cmap = doc.xref_stream(cmap_ref)
            gid = page.get_texttrace()[0]['chars'][0][1]
            # The inserted Type0 Identity-H font uses glyph IDs as CIDs. A later
            # bfchar entry overrides a previous range or character mapping.
            override = f'1 beginbfchar\n<{gid:04x}> <{ord(corrupt):04x}>\nendbfchar\n'.encode()
            doc.update_stream(cmap_ref, cmap.replace(b'endcmap', override+b'endcmap'))
        return doc.tobytes()


def test_cmsy_calligraphic_letter_is_not_a_plain_or_blackboard_variable():
    pdf = _symbol_pdf('smc_sd', 'CMSY10', 'X')
    assert _search(pdf, r'\mathcal{X}', mode='exact')
    assert not _search(pdf, 'X', mode='exact')
    assert not _search(pdf, r'\mathbb{X}', mode='exact')


def test_pazo_blackboard_bold_is_not_ordinary_bold():
    pdf = _symbol_pdf('smc_sd', 'PazoMathBlackboardBold', 'E')
    assert _search(pdf, r'\mathbb{E}', mode='exact')
    assert not _search(pdf, r'\mathbf{E}', mode='exact')
    assert not _search(pdf, 'E', mode='exact')


@pytest.mark.parametrize('corrupt', [None, 'X'])
def test_txsyb_named_blackboard_letter_keeps_its_alphabet_and_glyph_identity(corrupt):
    pdf = _symbol_pdf('mamba', 'txsyb', 'R', corrupt=corrupt)
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        assert (corrupt or 'R') in doc[0].get_text()
    result, = _search(pdf, r'\mathbb{R}', mode='exact')
    assert result.bbox[0] == pytest.approx(50)
    assert _search(pdf, 'ℝ', mode='exact')
    for wrong in ['R', r'\mathbf{R}', r'\mathcal{R}', r'\mathbb{X}']:
        assert not _search(pdf, wrong, mode='exact')


def test_unrecognized_font_name_does_not_imply_blackboard_from_plain_letter():
    pdf = _symbol_pdf('mamba', 'txsyb', 'R')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        xref = doc[0].get_fonts()[0][0]
        doc.xref_set_key(xref, 'BaseFont', '/UnknownTXSymbolFont')
        pdf = doc.tobytes()
    assert not _search(pdf, r'\mathbb{R}')


@pytest.mark.parametrize('same_font', [False, True])
def test_blackboard_repair_requires_same_named_font_resources_to_agree(same_font):
    pdf = _symbol_pdf('mamba', 'txsyb', 'R')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        page = doc[0]
        font = _embedded('mamba', 'txsyb') if same_font else _embedded('gan_tutorial', 'CMR10')
        xref = page.insert_font(fontname='OTHER', fontbuffer=font)
        doc.xref_set_key(xref, 'BaseFont', '/txsyb')
        page.insert_text((100, 100), 'R', fontname='OTHER', fontsize=12)
        pdf = doc.tobytes()
    matches = _search(pdf, r'\mathbb{R}', mode='exact')
    assert bool(matches) == same_font


@pytest.mark.parametrize(('paper', 'font', 'symbol', 'wrong', 'query'), [
    ('gan_tutorial', 'CMSY6', '∗', '⇤', r'\ast'),
    ('gan_tutorial', 'CMMI10', 'θ', '✓', r'\theta'),
    ('logz', 'LMMathItalic10-Regular', 'φ', '✓', r'\phi'),
    ('logz', 'LMMathItalic10-Regular', 'ϕ', '✓', r'\varphi'),
])
def test_wrong_unicode_map_uses_the_actual_named_glyph(paper, font, symbol, wrong, query):
    pdf = _symbol_pdf(paper, font, symbol, corrupt=wrong)
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        assert wrong in doc[0].get_text()
    matches = _search(pdf, query, mode='exact')
    assert len(matches) == 1
    assert not _search(pdf, wrong, mode='exact')
    assert matches[0].bbox_source == 'query-subspan'
    assert matches[0].bbox[0] == pytest.approx(50)


@pytest.mark.parametrize(('text', 'query', 'wrong'), [('µ', r'\mu', 'm'), ('ε', r'\varepsilon', r'\epsilon'), ('ϕ', r'\phi', r'\varphi')])
def test_unicode_symbols_from_other_fonts_keep_their_visible_shape(text, query, wrong):
    with fitz.open() as doc:
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        writer.append((50, 100), text+'=1', fontsize=12)
        writer.write_text(page)
        pdf = doc.tobytes()
    assert _search(pdf, query+'=1', mode='exact')
    assert not _search(pdf, wrong+'=1')


def test_unknown_glyph_name_does_not_use_an_unrelated_unicode_repair():
    # A real Unicode arrow in a regular font must never become an asterisk.
    with fitz.open() as doc:
        p = doc.new_page()
        writer = fitz.TextWriter(p.rect)
        writer.append((50, 100), '⇤=1', fontsize=12)
        writer.write_text(p)
        pdf = doc.tobytes()
    assert not _search(pdf, r'\ast=1')
    assert _search(pdf, '⇤=1', mode='exact')


@pytest.mark.parametrize('same_font', [False, True])
def test_same_named_font_resources_must_agree_before_repair(same_font):
    pdf = _symbol_pdf('gan_tutorial', 'CMMI10', 'θ', corrupt='✓')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        page = doc[0]
        other_paper = 'gan_tutorial' if same_font else 'smc_sd'
        font = _embedded(other_paper, 'CMMI10')
        xref = page.insert_font(fontname='OTHER', fontbuffer=font)
        doc.xref_set_key(xref, 'BaseFont', '/CMMI10')
        page.insert_text((100, 100), 'x', fontname='OTHER', fontsize=12)
        pdf = doc.tobytes()
    matches = _search(pdf, r'\theta', mode='exact')
    assert bool(matches) == same_font


def _mapsto_pdf(reverse=False, bar_x=62, arrow=True, missing_unicode=False):
    buffer = _embedded('logz', 'LMMathSymbols10-Regular')
    font = fitz.Font(fontbuffer=buffer)
    with fitz.open() as doc:
        page = doc.new_page()
        xref = page.insert_font(fontname='MATH', fontbuffer=buffer)
        doc.xref_set_key(xref, 'BaseFont', '/LMMathSymbols10-Regular')
        page.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
        parts = [('mapsto', bar_x)] + ([('arrowright', 62)] if arrow else [])
        for name, x in reversed(parts) if reverse else parts:
            gid = font.this.fz_encode_character_by_glyph_name(name)
            assert gid
            page.insert_text((x, 100), '→', fontname='MATH', fontsize=12)
            stream = f'q BT /MATH 12 Tf 1 0 0 1 {x} {page.rect.height-100} Tm <{gid:04x}> Tj ET Q'.encode()
            doc.update_stream(page.get_contents()[-1], stream)
        page.insert_text((78, 100), 'y', fontname='tiit', fontsize=12)
        cmap_ref = int(doc.xref_get_key(xref, 'ToUnicode')[1].split()[0])
        gid = font.this.fz_encode_character_by_glyph_name('mapsto')
        code = 'fffd' if missing_unicode else '0037'
        override = f'1 beginbfchar\n<{gid:04x}> <{code}>\nendbfchar\n'.encode()
        doc.update_stream(cmap_ref, doc.xref_stream(cmap_ref).replace(b'endcmap', override+b'endcmap'))
        return doc.tobytes()


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('missing_unicode', [False, True])
def test_mapsto_bar_is_not_a_digit_and_survives_either_paint_order(reverse, missing_unicode):
    pdf = _mapsto_pdf(reverse=reverse, missing_unicode=missing_unicode)
    assert _search(pdf, r'x\mapsto y', mode='exact')
    assert _search(pdf, 'x↦y', mode='exact')
    assert not _search(pdf, r'x7\to y')
    assert not _search(pdf, r'x\to y')


@pytest.mark.parametrize('kwargs', [{'bar_x': 50}, {'arrow': False}])
def test_mapsto_requires_the_arrow_at_the_bar(kwargs):
    assert not _search(_mapsto_pdf(**kwargs), r'\mapsto')
