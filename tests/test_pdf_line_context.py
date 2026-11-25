"""Context parity at row boundaries, ties and unusual PDF coordinates."""
import math
import random

import pytest

from backend.extractor.arxiv_tex import _PdfLine, _set_page_line_context


def line(i, x, y, text=None):
    return _PdfLine(page=0, text=str(i) if text is None else text,
                    bbox=(x, y, x+1, y+1), page_w=600, page_h=800,
                    norm=str(i), norm_chars=[], tokens=set(), line_index=i)


def reference_context(lines):
    # The original exhaustive definition is an independent correctness oracle.
    result = []
    for i, item in enumerate(lines):
        peers = [other for other in lines if abs(other.y0-item.y0) <= 3.5]
        pieces = ([lines[i-1].text] if i else [])
        pieces += [peer.text for peer in sorted(peers, key=lambda peer: peer.x0)]
        pieces += [lines[i+1].text] if i+1 < len(lines) else []
        result.append(' '.join(dict.fromkeys(pieces)))
    return result


@pytest.mark.parametrize('ys', [
    [], [1.0], [0.0, -0.0, 0.0, 0.0],
    [-7.0, -3.5, 0.0, 3.5, 7.0],
    [math.nextafter(-3.5, -math.inf), -3.5, math.nextafter(-3.5, math.inf),
     0.0, math.nextafter(3.5, -math.inf), 3.5, math.nextafter(3.5, math.inf)],
    [-1e308, -1e20, 1e20, 1e308],
    [0.0, float('nan'), 1.0, float('inf'), float('-inf')],
])
def test_context_matches_exhaustive_definition(ys):
    lines = [line(i, i % 3, y, text=str(i % 4)) for i, y in enumerate(ys)]
    lines.sort(key=lambda item: (item.y0, item.x0, item.line_index))
    expected = reference_context(lines)
    _set_page_line_context(lines)
    assert [item.nearby_text for item in lines] == expected


def test_dense_table_and_two_column_context_matches_exhaustive_definition():
    rng = random.Random(48713)
    lines = [line(i, rng.choice([40, 100, 120, 300, 440]),
                  rng.choice([0.0, 0.1, 3.5, 3.6, 7.1, 12.0, 100.0]) + rng.randrange(30)*20,
                  text=f'cell {i % 19}') for i in range(1800)]
    lines.sort(key=lambda item: (item.y0, item.x0, item.line_index))
    expected = reference_context(lines)
    _set_page_line_context(lines)
    assert [item.nearby_text for item in lines] == expected
