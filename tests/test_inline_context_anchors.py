"""Immediate prose must distinguish nearby repeated inline formulas."""
import fitz
import pytest

from backend.extractor import arxiv_tex as tex
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index


def mapped(source, lines):
    with fitz.open() as doc:
        page = doc.new_page()
        for i, text in enumerate(lines):
            page.insert_text((60, 100 + i * 25), text, fontsize=12, fontname='tiit')
        candidates = tex.parse_tex_formulas(source)
        tex.assign_bboxes_from_pdf(candidates, doc.tobytes())
        return candidates


@pytest.mark.parametrize('reverse', [False, True])
def test_adjacent_prose_distinguishes_repeated_formula_in_nearby_lines(reverse):
    rows = [('The first estimate $r_{01}$ has low variance.',
             'The first estimate r01 has low variance.'),
            ('The second estimate $r_{01}$ has high variance.',
             'The second estimate r01 has high variance.')]
    if reverse:
        rows.reverse()
    candidates = mapped('\n'.join(r[0] for r in rows), [r[1] for r in rows])
    assert len(candidates) == 2
    assert [c.pdf_line_text for c in candidates] == [r[1] for r in rows]
    matches = search_formulas(r'r_{01}', build_index(candidates), mode='exact')
    assert len(matches) == 2 and all(m.bbox is not None for m in matches)
    assert matches[0].bbox != matches[1].bbox


def test_matching_prose_without_the_formula_does_not_prove_a_location():
    candidates = mapped('The random variable $r_{01}$ has expectation zero.',
                        ['The random variable z02 has expectation zero.',
                         'Some other estimate r01 has different properties.'])
    assert candidates[0].pdf_line_text != 'The random variable z02 has expectation zero.'


def test_adjacent_prose_selects_the_correct_occurrence_within_one_line():
    candidates = mapped('The first estimate $r_{01}$ has low variance; the second estimate $r_{01}$ has high variance.',
                        ['The first estimate r01 has low variance; the second estimate r01 has high variance.'])
    assert len(candidates) == 2 and all(c.bbox is not None for c in candidates)
    assert candidates[0].bbox[2] < candidates[1].bbox[0]
    matches = search_formulas(r'r_{01}', build_index(candidates), mode='exact')
    assert len(matches) == 2 and all(m.bbox is not None for m in matches)


def test_context_anchor_includes_formula_punctuation_but_excludes_neighboring_prose():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 100), 'The random variable (r01) has expectation zero.', fontsize=12, fontname='tiit')
        candidates = tex.parse_tex_formulas('The random variable $(r_{01})$ has expectation zero.')
        tex.assign_bboxes_from_pdf(candidates, doc.tobytes())
        expected = page.search_for('(r01)')[0]
        assert candidates[0].bbox is not None
        assert all(abs(a-b) < 0.1 for a,b in zip(candidates[0].bbox, expected))


def test_identical_prose_remains_ambiguous_instead_of_using_source_order():
    source = 'The random variable $r_{01}$ has expectation zero.'
    candidates = mapped(source + '\n' + source,
                        ['The random variable r01 has expectation zero.'] * 2)
    matches = search_formulas(r'r_{01}', build_index(candidates), mode='exact')
    assert len(matches) == 2
    assert all(m.bbox is None and m.bbox_source == 'ambiguous-source-location' for m in matches)


def test_ordered_text_cannot_join_distant_glyphs_into_one_formula():
    candidate = tex.parse_tex_formulas('The random variable $r_{01}$ has expectation zero.')[0]
    text = 'The random variable r01 has expectation zero.'
    boxes = [(float(i*5), 20., float(i*5+4), 30.) for i in range(len(text))]
    start = text.index('r01')
    boxes[start+1] = (400., 20., 404., 30.)
    boxes[start+2] = (405., 20., 409., 30.)
    chars = tex._norm_chars_from_text(text, boxes)
    line = tex._PdfLine(0, text, (0,20,500,30), 600, 800,
                        ''.join(c for c,_ in chars), chars, set(), raw_chars=list(zip(text, boxes)))
    assert tex._line_prose_anchor_bbox(tex._inline_prose_anchor(candidate, 'r01'), line) is None


def test_operator_font_descent_does_not_extend_highlight_into_next_line():
    candidate = tex.parse_tex_formulas('The random variable $r-01$ has expectation zero.')[0]
    text = 'The random variable r-01 has expectation zero.'
    boxes = [(float(i*5), 20., float(i*5+4), 30.) for i in range(len(text))]
    minus = text.index('-')
    boxes[minus] = (float(minus*5), 20., float(minus*5+4), 47.)
    chars = tex._norm_chars_from_text(text, boxes)
    line = tex._PdfLine(0, text, (0,20,500,47), 600, 800,
                        ''.join(c for c,_ in chars), chars, set(), raw_chars=list(zip(text, boxes)))
    box = tex._line_prose_anchor_bbox(tex._inline_prose_anchor(candidate, 'r01'), line)
    assert box is not None and box[1] == 20 and box[3] == 30
