import pytest
from sympy import Function, Symbol

from backend.matcher import ast_match, semantic


def test_parse_preserves_explicit_multiplication_and_function_application():
    a, x, y, f = map(Symbol, ['a', 'x', 'y', 'f'])
    product = ast_match.parse_query('a*(x+y)^2')
    function = ast_match.parse_query('f(x)^2')
    assert product.free_symbols == {a, x, y}
    assert product.equals(a*(x+y)**2) is True
    assert function == Function('f')(x)**2
    assert not ast_match.semantically_equal(function, ast_match.parse_query('f^2*x'))


@pytest.mark.parametrize('query,candidate,expected', [
    ('a*(x+y)^2', 'a*x^2+2*a*x*y+a*y^2', [0]),
    ('a(x+y)^2', 'a^2*(x+y)', []),
    ('f(x)^2', 'f^2*x', []),
    ('x-x+y-y+z', 'z', [0]),
    (r'q(\lambda)^2', r'q(\lambda)*q(\lambda)', [0]),
    (r'q(\lambda)^2', r'q^2*\lambda', []),
])
def test_scalar_worker_does_not_change_meaning_or_reject_expansion(query, candidate, expected, monkeypatch):
    # Mathematical assertions require completed work, independently of startup
    # variability. The existing kill/reap test enforces the production 4s limit.
    monkeypatch.setattr(semantic, 'WORKER_TIMEOUT_SECONDS', 15)
    indices, status = semantic.equivalent_candidates(query, [(0, candidate)], budget=1.5)
    assert status == 'complete'
    assert indices == expected


def test_structural_highlight_preserves_function_arguments_and_repeated_operands():
    assert ast_match.ast_partial_match('y+x', 'a*(x+y)^2') is not None
    assert ast_match.ast_partial_match('f(x,y)', 'f(y,x)') is None
    assert ast_match.ast_partial_match('x+x', 'x+y') is None


def test_skipped_unsupported_and_over_budget_candidates_are_reported_incomplete(monkeypatch):
    monkeypatch.setattr(semantic, 'WORKER_TIMEOUT_SECONDS', 15)
    candidates = [(0, r'\frac{1{'), (1, 'y+x'), (2, '+'.join(f'x_{i}' for i in range(45)))]
    indices, status = semantic.equivalent_candidates('x+y', candidates, budget=1.5)
    assert indices == [1]
    assert status == 'limited'


def test_reused_worker_keeps_meaning_and_indices_across_different_requests(monkeypatch):
    monkeypatch.setattr(semantic, 'WORKER_TIMEOUT_SECONDS', 15)
    cases = [
        ('a*(x+y)^2', [(17, 'a*x^2+2*a*x*y+a*y^2')], [17]),
        ('f(x)^2', [(22, 'f^2*x')], []),
        (r'q(\lambda)^2', [(9, r'q(\lambda)*q(\lambda)'), (8, r'q^2*\lambda')], [9]),
        ('x-x+y-y+z', [(7, 'z')], [7]),
    ]
    pids = []
    for query, candidates, expected in cases:
        assert semantic.equivalent_candidates(query, candidates, budget=1.5) == (expected, 'complete')
        pids.append(semantic._get_pool().snapshot()['workers'][0]['pid'])
    assert len(set(pids)) == 1
    assert semantic._get_pool().snapshot()['workers'][0]['requests'] == len(cases)
