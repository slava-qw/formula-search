from pathlib import Path
import pytest

from backend.extractor.arxiv_tex import parse_tex_formulas, _merge_tex_files
from tools.audit_formula_mapping import _load_tex_files


def formulas(tex):
    return [candidate.latex for candidate in parse_tex_formulas(tex)]


def test_currency_dollars_do_not_desynchronize_math():
    assert formulas(r'Costs \$50 and \$100, then $x+y$ and $a+b$.') == ['x+y', 'a+b']


def test_all_four_standard_delimiters_are_supported():
    assert formulas(r'$a+b$ \(c+d\) $$e+f$$ \[g+h\]') == ['a+b', 'c+d', 'e+f', 'g+h']
    assert formulas(r'$a+b$$c+d$') == ['a+b', 'c+d']


def test_verbatim_and_inactive_branches_do_not_become_formulas():
    text = r'\verb|$bad$| \begin{verbatim}$also$\end{verbatim} \iffalse $a+b$ \iftrue $e+f$\fi \else $c+d$\fi'
    assert formulas(text) == ['c+d']


def test_delimiters_inside_equations_do_not_corrupt_later_inline_math():
    text = r'\begin{equation} x=\text{\$50}\end{equation} Outside $a+b$.'
    assert formulas(text) == [r'x=\text{\$50}', 'a+b']


def test_unclosed_inline_math_cannot_capture_whole_paragraphs():
    assert formulas('$unfinished\n\nprose follows.\n\nValid $x+y$.') == ['x+y']


def test_includes_resolve_under_archive_root_and_allow_repeated_use():
    files = {'paper/main.tex': r'\documentclass{article}\begin{document}\input{parts/eq}\input{parts/eq}\end{document}',
             'paper/parts/eq.tex': r'$x+y$ \input{tail}', 'paper/parts/tail.tex': r'$c+d$'}
    assert formulas(_merge_tex_files(files)) == ['x+y', 'c+d', 'x+y', 'c+d']


def test_include_cycles_and_inactive_includes_are_skipped():
    files = {'main.tex': r'\documentclass{article}\begin{document}\iffalse\input{draft}\fi\input{a}\end{document}',
             'draft.tex': '$draft+1$', 'a.tex': r'$x+y$\input{main}'}
    assert formulas(_merge_tex_files(files)) == ['x+y']


def test_optional_macro_default_and_override():
    text = r'\newcommand{\pair}[2][x]{#1+#2}\begin{document}$\pair{y}$ $\pair[z]{y}$\end{document}'
    assert formulas(text) == ['x+y', 'z+y']


def test_macro_expansion_preserves_control_words_and_following_operators():
    text = r'\newcommand{\a}{\alpha}\newcommand{\c}{x}\begin{document}$\a y$ $\c*z$ $\c[0]$\end{document}'
    assert formulas(text) == [r'\alpha y', 'x*z', 'x[0]']


def test_bold_and_declared_operators_do_not_gain_a_second_backslash():
    text = r'\DeclareMathOperator{\argmin}{argmin}\begin{document}$\bm{x}$ $\argmin_x f(x)$\end{document}'
    assert formulas(text) == [r'\boldsymbol{x}', r'\operatorname{argmin}_x f(x)']


def test_standalone_symbols_and_numeric_debris():
    assert formulas(r'$x$ $Q$ $\mathbf{w}$ $1$ $^{12}$ $.$') == ['x', 'Q', r'\mathbf{w}']


def test_llama_source_does_not_index_pages_of_prose_as_formulas():
    path = Path(__file__).resolve().parents[1] / 'testdata/benchmarks/artifacts/2307.09288v2.tar.gz'
    if not path.exists():
        pytest.skip('Restore versioned corpus with tools/fetch_paper_fixtures.py')
    candidates = parse_tex_formulas(_merge_tex_files(_load_tex_files(path)))
    assert max(len(c.latex) for c in candidates) < 4000
    assert any(r'\mathcal{L}_{\text{ranking}}' in c.latex for c in candidates)
    assert any(r'\pi_{\theta}' in c.latex for c in candidates)
