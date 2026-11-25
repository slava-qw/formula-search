import pytest

from backend.extractor.arxiv_tex import parse_tex_formulas
from backend.extractor.tex_macros import expand_macros, ExpansionLimitError, read_group


def formulas(body, preamble=r'\newcommand{\a}{x}'):
    return [c.latex for c in parse_tex_formulas(preamble + r'\begin{document}' + body + r'\end{document}')]


def test_redefinition_does_not_change_earlier_formulas():
    assert formulas(r'$\a+1$\renewcommand{\a}{y}$\a+1$') == ['x+1', 'y+1']


@pytest.mark.parametrize('opening,closing', [('{', '}'), (r'\begingroup', r'\endgroup'),
                                           (r'\begin{quote}', r'\end{quote}')])
def test_local_macro_definition_is_restored_after_group(opening, closing):
    assert formulas(opening + r'\renewcommand{\a}{y}$\a+1$' + closing + r'$\a+1$') == ['y+1', 'x+1']


def test_math_mode_is_a_local_definition_scope():
    assert formulas(r'$\def\a{y}\a+1$ $\a+1$') == ['y+1', 'x+1']


def test_gdef_survives_a_local_group():
    assert formulas(r'{\gdef\a{y}$\a+1$} $\a+1$') == ['y+1', 'y+1']


def test_definition_order_is_preserved_across_declaration_types():
    assert formulas(r'$\a$\renewcommand{\a}{y}$\a$\def\a{z}$\a$', preamble=r'\def\a{x}') == ['x', 'y', 'z']


def test_unused_macro_body_is_not_an_occurrence():
    assert formulas(r'\newcommand{\unused}{$z+99$}$x+1$') == ['x+1']


def test_providecommand_does_not_replace_existing_definition():
    assert formulas(r'\providecommand{\a}{y}$\a$') == ['x']


def test_edef_captures_current_expansion_but_def_retains_dynamic_reference():
    assert formulas(r'\edef\b{\a}\def\c{\a}\def\a{y}$\b+\c$') == ['x+y']


def test_nested_macro_calls_expand_without_global_reparse():
    assert formulas(r'$\twice{\twice{x}}$', preamble=r'\newcommand{\twice}[1]{#1+#1}') == ['x+x+x+x']


def test_nested_definition_parameter_hashes_are_preserved():
    assert formulas(r'\outer{y}$\inner{x}$', preamble=r'\newcommand{\outer}[1]{\newcommand{\inner}[1]{##1+#1}}') == ['x+y']


def test_unsupported_delimited_definition_does_not_reuse_stale_meaning():
    assert formulas(r'\def\a#1,#2{#1+#2}$\a x,y$') == [r'\a x,y']


def test_escaped_braces_and_brackets_inside_arguments():
    assert read_group(r'{a\}b}', 0) == (r'a\}b', 6)
    assert formulas(r'$\pair[{]}]{y}$', preamble=r'\newcommand{\pair}[2][x]{#1+#2}') == ['{]}+y']


def test_recursive_and_explosive_macros_have_explicit_limits():
    with pytest.raises(ExpansionLimitError, match='recursion'):
        expand_macros(r'\def\loop{\loop}\loop', max_depth=8)
    with pytest.raises(ExpansionLimitError, match='size'):
        expand_macros(r'\newcommand{\many}[1]{#1#1#1#1#1#1#1#1}\many{\many{\many{abcdef}}}', max_work_chars=300)


def test_ensuremath_creates_math_only_when_outside_math_mode():
    assert formulas(r'\ensuremath{x+y} and $\ensuremath{a+b}+c$') == ['x+y', 'a+b+c']
    assert formulas(r'\begin{equation}\ensuremath{x+y}=z\end{equation}') == ['x+y=z']
