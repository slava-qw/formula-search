"""Span fidelity for accelerated notation scans, including reordered source spans."""
import random

import pytest

from backend.matcher.notation import Notation, Piece, find_notation, notation


@pytest.mark.parametrize('text,query,expected', [
    ('x+x+x+y', 'x+y', (4, 7)),
    ('abababa', 'ababa', (0, 5)),
    ('x', '', None),
    ('', 'x', None),
    ('x+y', 'x+y+z', None),
    ('312', '12', None),
    (r'\alphab', r'\alpha', None),
    (r'\alpha b', r'\alpha', (0, 6)),
    ('a'*100+'z', 'z', (100, 101)),
    ('x'*100+'y', 'x'*15+'y', (85, 101)),
    ('x'*100, 'x'*15+'y', None),
])
def test_scan_returns_first_complete_original_span(text, query, expected):
    assert find_notation(notation(query).tokens, notation(text)) == expected


def test_reordered_scripts_still_union_original_source_positions():
    parsed = notation('x^2_i')
    canonical = notation('x_i^2')
    assert parsed.tokens == canonical.tokens
    # Canonical '_' appears before '^', but its source position is later.
    assert find_notation(canonical.tokens[1:], parsed) == (1, 5)
    assert find_notation(canonical.tokens, parsed) == (0, 5)


def test_single_token_preserves_zero_width_synthetic_group_span():
    parsed = Notation(('{', 'x', '}'), (Piece('{', 4, 4), Piece('x', 4, 5), Piece('}', 5, 5)))
    assert find_notation(('{',), parsed) == (4, 4)
    assert find_notation(('}',), parsed) == (5, 5)


def test_seeded_spans_match_exhaustive_search_across_overlaps_and_nonmonotone_positions():
    rng = random.Random(20261122)
    alphabet = ('x', 'y', '+', r'\alpha', '{', '}', '_', '^', '12')
    for trial in range(1500):
        tokens = tuple(rng.choice(alphabet) for _ in range(rng.randrange(100)))
        positions = list(range(len(tokens)))
        rng.shuffle(positions)
        pieces = tuple(Piece(token, start, start+rng.randrange(4)) for token, start in zip(tokens, positions))
        candidate = Notation(tokens, pieces)
        if tokens and trial % 2:
            start = rng.randrange(len(tokens))
            query = tokens[start:start+rng.randrange(1, 18)]
        else:
            query = tuple(rng.choice(alphabet) for _ in range(rng.randrange(18)))
        matches = [i for i in range(len(tokens)-len(query)+1) if query and tokens[i:i+len(query)] == query]
        expected = None
        if matches:
            selected = pieces[matches[0]:matches[0]+len(query)]
            expected = min(piece.start for piece in selected), max(piece.end for piece in selected)
        assert find_notation(query, candidate) == expected, (trial, query, tokens)
