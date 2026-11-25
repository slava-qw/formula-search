"""Accents from real font glyphs and deliberately ambiguous PDF geometry."""
import fitz
import pytest

from backend.extractor.text_layer import extract_formulas
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index
from tests.test_pdf_fonts import _embedded
from tests.test_pdf_math import _pdf, _search


MARKS = [('\u02c6', r'\hat'), ('\u02dc', r'\tilde'), ('\u00af', r'\bar'), ('\u20d7', r'\vec')]


def _mark(page, text, x=50, y=100, size=12):
    vector = text == '\u20d7'
    name = 'CMMI10' if vector else 'LMRoman10-Regular'
    buffer = _embedded('gan_tutorial' if vector else 'logz', name)
    resource = 'VECTOR' if vector else 'ACCENT'
    xref = page.insert_font(fontname=resource, fontbuffer=buffer)
    page.parent.xref_set_key(xref, 'BaseFont', '/'+name)
    page.insert_text((x, y), 'x' if vector else text, fontname=resource, fontsize=size)
    if vector:
        # This subset has the actual vector glyph but no Unicode encoding for
        # it. Paint its glyph ID with an explicit, correct ToUnicode mapping.
        gid = fitz.Font(fontbuffer=buffer).this.fz_encode_character_by_glyph_name('vector')
        assert gid
        stream = f'q BT /{resource} {size} Tf 1 0 0 1 {x} {page.rect.height-y} Tm <{gid:04x}> Tj ET Q'.encode()
        page.parent.update_stream(page.get_contents()[-1], stream)
        cmap_ref = int(page.parent.xref_get_key(xref, 'ToUnicode')[1].split()[0])
        cmap = page.parent.xref_stream(cmap_ref)
        override = f'1 beginbfchar\n<{gid:04x}> <20d7>\nendbfchar\n'.encode()
        page.parent.update_stream(cmap_ref, cmap.replace(b'endcmap', override+b'endcmap'))


@pytest.mark.parametrize(('symbol', 'command'), MARKS)
@pytest.mark.parametrize('reverse', [False, True])
def test_accent_survives_paint_order_and_mapping_covers_both_glyphs(symbol, command, reverse):
    def write(p):
        actions = [lambda: _mark(p, symbol), lambda: p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)]
        for action in reversed(actions) if reverse else actions:
            action()
    pdf = _pdf(write)
    match, = _search(pdf, command+'{x}', mode='exact')
    assert not _search(pdf, 'x', mode='exact')
    for _, other in MARKS:
        if other != command:
            assert not _search(pdf, other+'{x}')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        boxes = [c['bbox'] for b in doc[0].get_text('rawdict')['blocks'] for l in b.get('lines', [])
                 for s in l['spans'] for c in s['chars'] if not c['c'].isspace()]
        expected = fitz.Rect(boxes[0])
        for box in boxes[1:]:
            expected |= box
    assert match.bbox == pytest.approx(tuple(expected))
    assert match.bbox_source == 'query-subspan'
    candidate, = extract_formulas(pdf)
    assert ''.join(ch for ch, _ in candidate.pdf_latex_chars) == candidate.latex


@pytest.mark.parametrize(('y', 'script'), [(94, '^'), (104, '_')])
def test_script_is_outside_the_accent_and_highlight_excludes_next_term(y, script):
    def write(p):
        _mark(p, '\u02c6')
        p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((56, y), 'i', fontname='tiit', fontsize=8)
        p.insert_text((65, 100), '+y', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    query = r'\hat{x}'+script+'i'
    match, = _search(pdf, query)
    assert not _search(pdf, r'\hat{x'+script+'i}')
    assert not _search(pdf, 'x'+script+'i+y')
    assert match.bbox[2] < 65


def test_accent_inside_fraction_preserves_structure_and_cache_mapping(tmp_path, monkeypatch):
    from backend import cache
    monkeypatch.setattr(cache, 'CACHE_DIR', tmp_path)
    def write(p):
        _mark(p, '\u02c6', y=90)
        p.insert_text((50, 90), 'x', fontname='tiit', fontsize=12)
        p.insert_text((50, 110), 'y', fontname='tiit', fontsize=12)
        p.draw_line((49, 98), (59, 98), width=.4)
    pdf = _pdf(write)
    query = r'\frac{\hat{x}}{y}'
    assert _search(pdf, query, mode='exact')
    assert not _search(pdf, r'\frac{x}{y}')
    cache.store_candidates_cache('pdf-text', 'accents', extract_formulas(pdf))
    restored = cache.load_candidates_cache('pdf-text', 'accents')
    assert search_formulas(query, build_index(restored), mode='exact') == _search(pdf, query, mode='exact')


@pytest.mark.parametrize(('x', 'y', 'size'), [(62, 100, 12), (50, 90, 12), (50, 105, 12), (50, 100, 6)])
def test_mark_with_incompatible_position_or_size_is_not_attached(x, y, size):
    def write(p):
        _mark(p, '\u02c6', x=x, y=y, size=size)
        p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
    assert not _search(_pdf(write), r'\hat{x}')


@pytest.mark.parametrize('right_font', ['tiit', 'helv'])
def test_two_possible_arguments_do_not_pick_one_even_if_other_is_roman(right_font):
    def write(p):
        _mark(p, '\u02c6', x=53)
        p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((56, 100), 'y', fontname=right_font, fontsize=12)
    pdf = _pdf(write)
    assert not _search(pdf, r'\hat{x}')
    assert not _search(pdf, r'\hat{y}')
    assert not _search(pdf, r'\hat{xy}')


@pytest.mark.parametrize('outer_y', [97, 92, 88])
def test_stacked_accents_are_not_partially_guessed(outer_y):
    def write(p):
        _mark(p, '\u02c6')
        _mark(p, '\u02dc', y=outer_y)
        p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    assert not _search(pdf, r'\hat{x}')
    assert not _search(pdf, r'\tilde{x}')


@pytest.mark.parametrize('font', ['helv', 'tiit'])
def test_accented_name_is_not_a_math_variable(font):
    def write(p):
        p.insert_text((50, 100), 'Joao', fontname=font, fontsize=12)
        x = 50+fitz.get_text_length('Jo', fontname=font, fontsize=12)
        _mark(p, '\u02dc', x=x)
    assert not _search(_pdf(write), r'\tilde{a}')


def test_literal_tex_is_preserved():
    pdf = _pdf(lambda p: p.insert_text((50, 100), r'\hat{x}_i', fontsize=12))
    assert _search(pdf, r'\hat{x}_i', mode='exact')


def test_greek_variable_in_a_regular_unicode_font():
    def write(p):
        _mark(p, '\u02dc')
        writer = fitz.TextWriter(p.rect)
        writer.append((50, 100), '\u03c0', fontsize=12)
        writer.write_text(p)
    assert _search(_pdf(write), r'\tilde{\pi}', mode='exact')
