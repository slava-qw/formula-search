"""Pruning must preserve anchor priority, ranking ties and float weighting."""
import pytest

from backend.extractor import arxiv_tex as tex


def resolve(monkeypatch, formula, rows, *, floating=False, region=None):
    candidate = tex.parse_tex_formulas(f'${formula}$')[0]
    lines = []
    evidence = {}
    for number, row in enumerate(rows):
        line = tex._PdfLine(row.get('page', 0), f'line {number}',
                            (10., 20., 40., 30.), 600., 800.,
                            row.get('norm', formula), [], set())
        lines.append(line)
        evidence[id(line)] = row
    calls = []
    def score(_norm, _tokens, line, _candidate, *, ctx=None):
        calls.append(lines.index(line))
        return evidence[id(line)]['score']
    monkeypatch.setattr(tex, '_similarity_score', score)
    monkeypatch.setattr(tex, '_context_score', lambda candidate, line: evidence[id(line)]['ctx'])
    monkeypatch.setattr(tex, '_is_float_or_table_candidate', lambda candidate: floating)
    monkeypatch.setattr(tex, '_table_line_from_hint', lambda candidate, lines: None)
    monkeypatch.setattr(tex, '_line_prose_anchor_bbox',
                        lambda anchor, line: line.bbox if evidence[id(line)].get('anchor') else None)
    monkeypatch.setattr(tex, '_tight_bbox_for_candidate_in_line', lambda candidate, line: line.bbox)
    match = tex._best_line_for_candidate(candidate, lines, region, 0)
    assert match is not None
    return lines.index(match[0]), calls


def test_impossible_winner_and_equal_full_rank_do_not_need_similarity(monkeypatch):
    winner, calls = resolve(monkeypatch, 'x', [
        {'score': 1., 'ctx': 1.}, {'score': 1., 'ctx': 0.}, {'score': 1., 'ctx': 1.}])
    assert winner == 0
    assert calls == [0]


def test_near_ceiling_incumbent_must_still_allow_a_better_score(monkeypatch):
    winner, calls = resolve(monkeypatch, 'x', [
        {'score': 0.999999999, 'ctx': 1.}, {'score': 1., 'ctx': 1.}])
    assert winner == 1
    assert calls == [0, 1]


def test_prose_anchor_can_win_with_lower_numeric_rank(monkeypatch):
    winner, calls = resolve(monkeypatch, 'abc', [
        {'score': 1., 'ctx': 1.},
        {'score': .9, 'ctx': 0., 'anchor': True},
        {'score': 1., 'ctx': 1.}])
    assert winner == 1
    assert calls == [0, 1]


def test_float_score_ceiling_uses_float_weights(monkeypatch):
    winner, calls = resolve(monkeypatch, 'x', [
        {'score': .6, 'ctx': 1.}, {'score': 1., 'ctx': 0.}], floating=True)
    assert winner == 1
    assert calls == [0, 1]


def test_region_tie_can_change_winner_at_the_same_score_ceiling(monkeypatch):
    winner, calls = resolve(monkeypatch, 'x', [
        {'score': 1., 'ctx': 1., 'page': 0},
        {'score': 1., 'ctx': 1., 'page': 1}], floating=True, region=((1, 0.), None))
    assert winner == 1
    assert calls == [0, 1]


@pytest.mark.parametrize('formula,rows', [
    ('abcde', [{'score': 1., 'ctx': .1},
               {'score': 1., 'ctx': 1., 'norm': 'prefixabcdesuffix'}]),
    ('abcdefghij', [{'score': 1., 'ctx': 1., 'norm': 'prefixabcdefghijsuffix'},
                    {'score': 1., 'ctx': 0.}]),
])
def test_equal_score_preserves_length_dependent_context_and_exact_ties(monkeypatch, formula, rows):
    winner, calls = resolve(monkeypatch, formula, rows)
    assert winner == 1
    assert calls == [0, 1]
