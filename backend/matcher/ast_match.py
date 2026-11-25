from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from functools import lru_cache
import importlib.metadata
import importlib.util
import re
from typing import Any, List, Optional, Sequence, Tuple

from sympy import Add, Mul, Function, count_ops, latex as sympy_latex, preorder_traversal
from sympy.printing.latex import LatexPrinter

_TOKEN_RE = re.compile(r'\\[a-zA-Z]+|[{}()\[\]^_]|[0-9]+|[^\s]')
_HIGHLIGHT_COLOR = "#f59e0b"

MAX_SEMANTIC_OPS = 40


class _Highlight(Function):
    @classmethod
    def eval(cls, x):
        return None  # prevent SymPy from simplifying away the wrapper


class _ColorPrinter(LatexPrinter):
    def _print__Highlight(self, expr, **kwargs):
        return r'\textcolor{' + _HIGHLIGHT_COLOR + r'}{' + self._print(expr.args[0]) + r'}'


@dataclass(frozen=True)
class _FallbackExpr:
    raw: str
    tokens: Tuple[str, ...]
    canonical: str


def tokenize(latex: str) -> List[str]:
    return _TOKEN_RE.findall(latex)


def _tokenize_with_spans(latex: str) -> List[Tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(latex)]


def _balanced_latex(expr: str) -> bool:
    stack: list[str] = []
    pairs = {'}': '{', ')': '(', ']': '['}
    for ch in expr:
        if ch in '{([':
            stack.append(ch)
        elif ch in '})]':
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


def _strip_outer_group(tokens: Sequence[str]) -> List[str]:
    tokens = list(tokens)
    changed = True
    while changed and len(tokens) >= 2:
        changed = False
        pairs = {('(', ')'), ('{', '}'), ('[', ']')}
        if (tokens[0], tokens[-1]) not in pairs:
            break
        depth = 0
        closes = {')': '(', '}': '{', ']': '['}
        valid = True
        for i, tok in enumerate(tokens):
            if tok in '({[':
                depth += 1
            elif tok in ')}]':
                depth -= 1
                if depth == 0 and i != len(tokens) - 1:
                    valid = False
                    break
                if depth < 0:
                    valid = False
                    break
        if valid and depth == 0:
            tokens = tokens[1:-1]
            changed = True
    return tokens


def _split_top_level(tokens: Sequence[str], operator: str) -> List[List[str]]:
    parts: List[List[str]] = [[]]
    depth = 0
    for tok in tokens:
        if tok in '({[':
            depth += 1
        elif tok in ')}]':
            depth -= 1
        if tok == operator and depth == 0:
            parts.append([])
        else:
            parts[-1].append(tok)
    return [p for p in parts if p]


def _canonical_tokens(tokens: Sequence[str]) -> str:
    tokens = _strip_outer_group(tokens)
    plus_parts = _split_top_level(tokens, '+')
    if len(plus_parts) > 1:
        return '+'.join(sorted(_canonical_tokens(p) for p in plus_parts))
    return ''.join(tokens)


def _fallback_expr(expr: str) -> Optional[_FallbackExpr]:
    expr = expr.strip()
    if not expr or not _balanced_latex(expr):
        return None
    tokens = tuple(tokenize(expr))
    if not tokens:
        return None
    return _FallbackExpr(raw=expr, tokens=tokens, canonical=_canonical_tokens(tokens))


def _sympy_latex_parser_available() -> bool:
    if importlib.util.find_spec("antlr4") is None:
        return False
    try:
        return importlib.metadata.version("antlr4-python3-runtime").startswith("4.11")
    except importlib.metadata.PackageNotFoundError:
        return False


def _parse_latex_sympy(expr: str) -> Optional[Any]:
    if not _sympy_latex_parser_available():
        return None
    try:
        from sympy.parsing.latex import parse_latex
        # strict=True: see the matching comment in normalizer.normalize_latex
        # -- the default permissive mode silently returns a partial parse
        # (e.g. just the leading symbol) for LaTeX it can't fully consume
        # instead of raising, which would otherwise make ast_highlight/
        # sympy_expr treat two unrelated formulas as structurally identical.
        return parse_latex(expr, strict=True)
    except Exception:
        return None


@lru_cache(maxsize=4096)
def parse_query(query: str) -> Optional[Any]:
    parsed = _parse_latex_sympy(query)
    if parsed is not None:
        # Printing and reparsing is not a semantics-preserving normalization:
        # a*(x+y)^2 prints with implicit multiplication, which ANTLR can read
        # as a function call. f(x)^2 can conversely become f^2*x. Keep the
        # original parsed tree. Structural highlighting handles commutative
        # argument order without converting the tree back into ambiguous text.
        return parsed
    return _fallback_expr(query)


def token_match(qt: List[str], ct: List[str]) -> bool:
    """Return True if qt appears as a contiguous sublist in ct. Both sides pre-tokenized."""
    if not qt:
        return False
    n = len(qt)
    return any(ct[i:i + n] == qt for i in range(len(ct) - n + 1))


def structural_signature(expr: Any) -> Optional[Tuple[int, int]]:
    """(free-symbol count, sympy.count_ops()) -- a cheap, order-independent
    shape fingerprint and complexity bound for scalar comparison.
    Returns None for anything that isn't a genuine
    parsed SymPy expression: parse_query() can also return a _FallbackExpr
    (when the optional ANTLR LaTeX parser isn't installed) or None
    outright, neither of which supports the SymPy methods this needs.
    """
    if expr is None or isinstance(expr, _FallbackExpr):
        return None
    try:
        return (len(expr.free_symbols), int(count_ops(expr)))
    except Exception:
        return None


def signatures_compatible(sig_a: Tuple[int, int], sig_b: Tuple[int, int]) -> bool:
    """Legacy shape heuristic retained for callers of the AST helper API.

    Not used to reject production scalar comparisons: valid expansion and
    cancellation can change both counts beyond these tolerances.
    """
    symbols_a, ops_a = sig_a
    symbols_b, ops_b = sig_b
    return abs(symbols_a - symbols_b) <= 1 and abs(ops_a - ops_b) <= 3


def semantically_equal(q_expr: Any, c_expr: Any) -> bool:
    """True if q_expr and c_expr are the same expression up to algebraic
    equivalence (SymPy's .equals()), gated by MAX_SEMANTIC_OPS on both
    sides to bound the cost of any single comparison. Returns False --
    never raises -- for anything that isn't a genuine parsed SymPy
    expression on both sides, and for any exception SymPy itself raises
    while checking equivalence (matches this file's existing defensive
    pattern around parser calls, e.g. _parse_latex_sympy).
    """
    if q_expr is None or c_expr is None:
        return False
    if isinstance(q_expr, _FallbackExpr) or isinstance(c_expr, _FallbackExpr):
        return False
    try:
        if count_ops(q_expr) > MAX_SEMANTIC_OPS or count_ops(c_expr) > MAX_SEMANTIC_OPS:
            return False
        return bool(q_expr.equals(c_expr))
    except Exception:
        return False


def _fallback_highlight(q_expr: _FallbackExpr, c_expr: _FallbackExpr) -> Optional[str]:
    q_len = len(q_expr.tokens)
    if q_len == 0 or len(c_expr.tokens) < q_len:
        return None

    candidate_spans = _tokenize_with_spans(c_expr.raw)
    candidate_tokens = [t for t, _, _ in candidate_spans]
    for i in range(len(candidate_tokens) - q_len + 1):
        window = candidate_tokens[i:i + q_len]
        if window == list(q_expr.tokens) or _canonical_tokens(window) == q_expr.canonical:
            start = candidate_spans[i][1]
            end = candidate_spans[i + q_len - 1][2]
            return (
                c_expr.raw[:start]
                + r'\textcolor{' + _HIGHLIGHT_COLOR + r'}{'
                + c_expr.raw[start:end]
                + '}'
                + c_expr.raw[end:]
            )
    return None


def ast_highlight(q_expr: Any, c_expr: Any) -> Optional[str]:
    """Return highlighted_latex given two pre-parsed expressions; None if no structural match.

    Uses SymPy when available and a deterministic token/canonical fallback when the
    optional ANTLR parser dependency is not installed. This keeps partial search
    useful in lightweight deployments instead of silently downgrading every query.
    """
    if q_expr is None or c_expr is None:
        return None

    if isinstance(q_expr, _FallbackExpr) or isinstance(c_expr, _FallbackExpr):
        if not isinstance(q_expr, _FallbackExpr):
            q_expr = _fallback_expr(sympy_latex(q_expr))
        if not isinstance(c_expr, _FallbackExpr):
            c_expr = _fallback_expr(sympy_latex(c_expr))
        if q_expr is None or c_expr is None:
            return None
        return _fallback_highlight(q_expr, c_expr)

    try:
        @lru_cache(maxsize=None)
        def shape(expr):
            if not expr.args:
                return expr
            children = tuple(shape(arg) for arg in expr.args)
            # A multiset preserves repeated operands, and only scalar
            # commutative Add/Mul permit reordering. Function arguments and
            # noncommutative multiplication retain their original order.
            if expr.func in (Add, Mul) and expr.is_commutative is True:
                return expr.func, frozenset(Counter(children).items())
            return expr.func, children

        query_shape = shape(q_expr)
        replacements = {node: _Highlight(node) for node in preorder_traversal(c_expr)
                        if shape(node) == query_shape}
        if not replacements:
            return None
        highlighted = c_expr.xreplace(replacements)
        if highlighted == c_expr:
            return None
        return _ColorPrinter().doprint(highlighted)
    except Exception:
        return None


def ast_partial_match_expr(q_expr: Any, candidate: str) -> Optional[str]:
    """Convenience wrapper: parses candidate string then calls ast_highlight."""
    c_expr = _parse_latex_sympy(candidate)
    if c_expr is None:
        c_expr = _fallback_expr(candidate)
    return ast_highlight(q_expr, c_expr)


def token_partial_match_tokens(qt: List[str], candidate: str) -> bool:
    """Convenience wrapper: tokenizes candidate string then calls token_match."""
    return token_match(qt, _TOKEN_RE.findall(candidate))


def ast_partial_match(query: str, candidate: str) -> Optional[str]:
    """Convenience wrapper; parses query on each call. Use ast_highlight in hot paths."""
    q_expr = parse_query(query)
    if q_expr is None:
        return None
    return ast_partial_match_expr(q_expr, candidate)


def token_partial_match(query: str, candidate: str) -> bool:
    """Convenience wrapper; tokenizes query on each call. Use token_match in hot paths."""
    return token_match(_TOKEN_RE.findall(query), _TOKEN_RE.findall(candidate))
