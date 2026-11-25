"""Conservative TeX notation keys with original source spans.

Canonicalize presentation spacing, known command aliases and script syntax,
preserving case, operators, fonts and grouping. This is not an algebra engine.
Lossy PDF-alignment keys must never be used to decide notation equivalence.
"""
from dataclasses import dataclass
from functools import lru_cache
import re
from backend.math_unicode import math_unicode_latex

_LEX = re.compile(r"\\[A-Za-z]+|\\.|[^\s]")
_IGNORE = {
    r"\left", r"\right", r"\big", r"\Big", r"\bigg", r"\Bigg",
    r"\bigl", r"\bigr", r"\Bigl", r"\Bigr", r"\biggl", r"\biggr",
    r"\,", r"\;", r"\:", r"\!", "\\ ", r"\quad", r"\qquad",
    r"\enspace", r"\thinspace", r"\displaystyle", r"\textstyle",
    r"\scriptstyle", r"\scriptscriptstyle", r"\limits", r"\nolimits",
}
_ALIASES = {r"\dfrac": r"\frac", r"\tfrac": r"\frac", r"\le": r"\leq",
            r"\ge": r"\geq", r"\ne": r"\neq", r"\widehat": r"\hat",
            r"\widetilde": r"\tilde", r"\lbrace": r"\{", r"\rbrace": r"\}", r"\ast": "*"}
_ARITY = {name: 1 for name in (
    r"\sqrt", r"\hat", r"\tilde", r"\bar", r"\vec", r"\dot", r"\ddot",
    r"\overline", r"\underline", r"\mathbf", r"\mathrm", r"\mathit",
    r"\mathbb", r"\mathcal", r"\mathsf", r"\mathtt", r"\mathscr",
    r"\mathfrak", r"\boldsymbol", r"\bm", r"\text", r"\textrm",
    r"\textnormal", r"\operatorname", r"\ensuremath",
)}
_ARITY.update({r"\frac": 2, r"\binom": 2, r"\overset": 2, r"\underset": 2})
_UNICODE = dict(zip("αβγδεζηθικλμνξπρστυφχψω", (
    "\\" + s for s in "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi chi psi omega".split()
)))
_UNICODE.update(dict(zip("ΓΔΘΛΞΠΣΥΦΨΩ", (
    "\\" + s for s in "Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega".split()
))))
_UNICODE.update({"∑": r"\sum", "∫": r"\int", "∞": r"\infty", "∂": r"\partial",
                 "∇": r"\nabla", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq",
                 "∈": r"\in", "∉": r"\notin",
                 "−": "-", "×": r"\times", "·": r"\cdot"})
_UNICODE.update({"ε": r"\varepsilon", "ϵ": r"\epsilon", "φ": r"\varphi", "ϕ": r"\phi",
                 "ϑ": r"\vartheta", "ϖ": r"\varpi", "ϱ": r"\varrho", "ς": r"\varsigma",
                 "ϰ": r"\varkappa", "µ": r"\mu", "∆": r"\Delta", "∗": "*", "↦": r"\mapsto", "→": r"\to",
                 "◯": r"\bigcirc", "∘": r"\circ", "▷": r"\triangleright", "◁": r"\triangleleft"})


@dataclass(frozen=True)
class Piece:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Notation:
    tokens: tuple[str, ...]
    pieces: tuple[Piece, ...]


def _group(parts, start, end):
    return [Piece("{", start, start), *parts, Piece("}", end, end)]


def _unicode_pieces(piece):
    if len(piece.value) == 1 and ord(piece.value) > 127:
        latex = math_unicode_latex(piece.value)
        if latex is not None:
            return [Piece(value, piece.start, piece.end) for value in notation(latex).tokens]
    return [piece]


@lru_cache(maxsize=8192)
def notation(text: str) -> Notation:
    raw = []
    for match in _LEX.finditer(text or ""):
        value = match.group()
        if value not in _IGNORE:
            raw.append(Piece(_ALIASES.get(value, _UNICODE.get(value, value)), *match.span()))

    def sequence(i, closing=None, depth=0):
        if depth > 64:
            raise ValueError("notation nesting limit")
        out = []
        while i < len(raw) and raw[i].value != closing:
            parts, i = atom(i, depth + 1)
            scripts = {}
            while i < len(raw) and raw[i].value in ("_", "^"):
                marker = raw[i]
                if marker.value in scripts or i + 1 == len(raw):
                    break
                arg, after = argument(i + 1, depth + 1)
                scripts[marker.value] = [marker, *arg]
                i = after
            out.extend(parts)
            for marker in ("_", "^"):
                out.extend(scripts.get(marker, []))
        return out, i

    def argument(i, depth):
        if i >= len(raw):
            return [], i
        first = raw[i]
        if first.value == "{":
            parts, after = sequence(i + 1, "}", depth)
            if after < len(raw):
                return _group(parts, first.start, raw[after].end), after + 1
            return [first, *parts], after
        # TeX's unbraced script argument is ONE token: x_12 means x_{1}2.
        return _group(_unicode_pieces(first), first.start, first.end), i + 1

    def atom(i, depth):
        first = raw[i]
        if first.value == "{":
            parts, after = argument(i, depth)
            # Authors often write K{+}1 to adjust TeX's operator spacing.
            # A group around one literal glyph does not change its notation.
            # Keep multi-token groups: {x+y}^2 has a different script base.
            if (len(parts) == 3 and parts[0].value == "{" and parts[-1].value == "}"
                    and len(parts[1].value) == 1 and parts[1].value not in "{}_^\\"):
                return parts[1:2], after
            return parts, after
        out = _unicode_pieces(first)
        i += 1
        if first.value == r"\sqrt" and i < len(raw) and raw[i].value == "[":
            out.append(raw[i])
            parts, after = sequence(i + 1, "]", depth)
            out.extend(parts)
            i = after
            if i < len(raw):
                out.append(raw[i]); i += 1
        for _ in range(_ARITY.get(first.value, 0)):
            if i == len(raw) or raw[i].value in ("}", "_", "^"):
                break
            parts, i = argument(i, depth)
            out.extend(parts)
        return out, i

    try:
        parsed, _ = sequence(0)
    except (ValueError, RecursionError):
        parsed = raw
    merged = []
    for part in parsed:
        if merged and merged[-1].value.isdecimal() and part.value.isdecimal():
            previous = merged.pop()
            merged.append(Piece(previous.value + part.value, previous.start, part.end))
        else:
            merged.append(part)
    return Notation(tuple(p.value for p in merged), tuple(merged))


def serialize(tokens) -> str:
    """Never merge a control word with a following letter."""
    out = []
    for token in tokens:
        if out and re.fullmatch(r"\\[A-Za-z]+", out[-1]) and token[:1].isalpha():
            out.append(" ")
        out.append(token)
    return "".join(out)


def find_notation(query: tuple[str, ...], candidate: Notation):
    if not query:
        return None
    n = len(query)
    stop = len(candidate.tokens) - n + 1
    start = 0
    # Skip impossible starting positions in C instead of allocating a tuple
    # slice for each token. Most searches miss or find their first token early.
    # Bound these probes: dense repeated prefixes favor the simple linear loop.
    for _ in range(8):
        if start >= stop:
            return None
        try:
            i = candidate.tokens.index(query[0], start, stop)
        except ValueError:
            return None
        if n == 1:
            part = candidate.pieces[i]
            return part.start, part.end
        if candidate.tokens[i:i + n] == query:
            parts = candidate.pieces[i:i + n]
            return min(p.start for p in parts), max(p.end for p in parts)
        start = i + 1
    for i in range(start, stop):
        if candidate.tokens[i:i + n] == query:
            parts = candidate.pieces[i:i + n]
            return min(p.start for p in parts), max(p.end for p in parts)
    return None


def highlight_notation(text: str, span) -> str:
    if span is None:
        return ""
    start, end = span
    selected = text[start:end]
    if selected in _ARITY or selected in _ALIASES:
        return ""  # a lone \frac cannot be wrapped separately from its arguments
    depth = 0
    for token in _LEX.findall(selected):
        if token == "{": depth += 1
        elif token == "}": depth -= 1
        if depth < 0: return ""
    if depth or not selected:
        return ""
    return text[:start] + r"{\textcolor{#f59e0b}{" + selected + "}}" + text[end:]
