"""Source extraction must not index known inactive etoolbox branches."""
import pytest

from backend.extractor.arxiv_tex import _merge_tex_files, parse_tex_formulas
from backend.extractor.tex_macros import ExpansionLimitError, expand_macros
from backend.extractor.tex_provenance import SourceText


def formulas(body, preamble=r'\newtoggle{arxiv}'):
    return [c.latex for c in parse_tex_formulas(preamble + r'\begin{document}' + body + r'\end{document}')]


def test_declaration_defaults_false_and_assignments_are_sequential():
    assert formulas(r'\iftoggle{arxiv}{$x$}{$y$}\toggletrue{arxiv}'
                    r'\iftoggle{arxiv}{$a$}{$b$}\togglefalse{arxiv}'
                    r'\nottoggle{arxiv}{$c$}{$d$}\settoggle{arxiv}{true}'
                    r'\nottoggle{arxiv}{$e$}{$f$}\settoggle{arxiv}{false}'
                    r'\iftoggle{arxiv}{$g$}{$h$}') == ['y', 'a', 'c', 'f', 'h']


def test_provide_and_duplicate_declarations_do_not_reset_state():
    assert formulas(r'\toggletrue{arxiv}\providetoggle{arxiv}\newtoggle{arxiv}'
                    r'\iftoggle{arxiv}{$x$}{$bad$}\providetoggle{fresh}'
                    r'\iftoggle{fresh}{$bad$}{$y$}') == ['x', 'y']


@pytest.mark.parametrize('opening,closing', [('{', '}'), (r'\begingroup', r'\endgroup'),
                                           (r'\begin{quote}', r'\end{quote}')])
def test_local_flag_assignment_restores_outer_state(opening, closing):
    assert formulas(opening + r'\toggletrue{arxiv}\iftoggle{arxiv}{$x$}{$bad$}' + closing
                    + r'\iftoggle{arxiv}{$bad$}{$y$}') == ['x', 'y']


def test_math_group_restores_toggle_state():
    assert formulas(r'$\toggletrue{arxiv}\iftoggle{arxiv}{x}{bad}$'
                    r'$\iftoggle{arxiv}{bad}{y}$') == ['x', 'y']


@pytest.mark.parametrize('assignment', [r'\toggletrue{arxiv}', r'\settoggle{arxiv}{true}'])
def test_global_assignment_survives_nested_local_groups(assignment):
    assert formulas(r'{\togglefalse{arxiv}{\global ' + assignment + '}}'
                    r'\iftoggle{arxiv}{$x$}{$bad$}') == ['x']


def test_selected_argument_braces_are_not_an_extra_scope():
    assert formulas(r'\iftoggle{arxiv}{\def\value{bad}}{\def\value{x}\toggletrue{arxiv}}'
                    r'$\value$\iftoggle{arxiv}{$y$}{$bad$}') == ['x', 'y']


def test_unselected_branch_and_unused_macro_do_not_execute():
    assert formulas(r'\def\unused{\toggletrue{arxiv}$unused$}'
                    r'\iftoggle{arxiv}{\global\toggletrue{arxiv}\gdef\value{bad}}{\def\value{x}}'
                    r'$\value$\iftoggle{arxiv}{$bad$}{$y$}') == ['x', 'y']


def test_macro_calls_use_current_flag_and_the_separate_namespace():
    assert formulas(r'\def\arxiv{z}\def\choose{\iftoggle{arxiv}{$x$}{$y$}}'
                    r'\choose\toggletrue{arxiv}\choose$\arxiv$') == ['y', 'x', 'z']


def test_nested_conditions_and_single_token_arguments():
    assert formulas(r'\newtoggle{t}\toggletrue{t}'
                    r'$\iftoggle t {\nottoggle{arxiv}{x}{bad}}{bad}$'
                    r'$\iftoggle{t}\alpha\beta+x$'
                    r'$\iftoggle{arxiv}xy$') == ['x', r'\alpha+x', 'y']


@pytest.mark.parametrize('body,expected', [
    (r'\alpha\iftoggle{arxiv}{bad}{x}', r'\alpha x'),
    (r'\iftoggle{arxiv}{bad}{\alpha}x', r'\alpha x'),
    (r'\alpha\iftoggle{arxiv}{}{}x', r'\alpha x'),
    (r'a\iftoggle{arxiv}{bad}{bc}', 'abc'),
])
def test_selected_token_boundaries(body, expected):
    assert formulas('$' + body + '$') == [expected]


def test_unknown_condition_is_opaque_and_forgets_possibly_changed_flags():
    source = (r'\newtoggle{t}\iftoggle{missing}{\global\toggletrue{t}\def\value{x}}'
              r'{\togglefalse{t}\def\value{y}}\iftoggle{t}{$a$}{$b$}')
    expanded = expand_macros(source)
    assert r'\iftoggle{missing}' in expanded and r'\iftoggle{t}{$a$}{$b$}' in expanded
    assert r'\def\value{x}' in expanded and r'\def\value{y}' in expanded
    # An uncertain global assignment or command definition must not become a
    # stale value after the group. Remaining toggle selection stays disabled.
    assert formulas(r'{\iftoggle{missing}{\global\toggletrue{arxiv}}{}}'
                    r'\iftoggle{arxiv}{$a$}{$b$}\toggletrue{arxiv}'
                    r'\iftoggle{arxiv}{$c$}{$bad$}') == ['a', 'b', 'c', 'bad']


def test_dynamic_names_and_values_are_not_guessed():
    assert formulas(r'\def\name{arxiv}\toggletrue{\name}'
                    r'\iftoggle{arxiv}{$a$}{$b$}\settoggle{arxiv}{\value}'
                    r'\iftoggle{arxiv}{$c$}{$d$}') == ['a', 'b', 'c', 'd']
    assert formulas(r'\toggletrue{missing}\iftoggle{missing}{$a$}{$b$}') == ['a', 'b']


def test_unknown_branch_might_have_declared_a_later_provided_toggle():
    assert formulas(r'\iftoggle{unknown}{\newtoggle{fresh}\toggletrue{fresh}}{}'
                    r'\providetoggle{fresh}\iftoggle{fresh}{$a$}{$b$}'
                    r'\toggletrue{fresh}\iftoggle{fresh}{$c$}{$d$}') == ['a', 'b', 'c', 'd']


def test_unknown_branch_command_definition_prevents_later_speculative_selection():
    assert formulas(r'\iftoggle{unknown}{\def\toggletrue#1{}}{}'
                    r'\toggletrue{arxiv}\iftoggle{arxiv}{$a$}{$b$}') == ['a', 'b']


def test_dynamic_toggle_assignment_can_be_followed_by_a_definite_value():
    assert formulas(r'\toggletrue{\name}\iftoggle{arxiv}{$a$}{$b$}') == ['a', 'b']
    assert formulas(r'\toggletrue{\name}\togglefalse{arxiv}'
                    r'\iftoggle{arxiv}{$bad$}{$x$}') == ['x']


@pytest.mark.parametrize('condition', [r'\ifnum\value{n}>0', r'\ifdefined\draft', r'\ifcustom', r'\if ab'])
def test_unsupported_condition_prevents_speculative_toggle_pruning(condition):
    assert formulas(condition + r'\toggletrue{arxiv}\else\togglefalse{arxiv}\fi'
                    r'\iftoggle{arxiv}{$a$}{$b$}') == ['a', 'b']


def test_unknown_condition_safety_survives_includes_slices_and_pickle():
    import pickle
    files = {'main.tex': r'\documentclass{article}\newtoggle{t}\input{flags}'
             r'\begin{document}\iftoggle{t}{$a$}{$b$}\end{document}',
             'flags.tex': r'\ifnum\value{n}>0\toggletrue{t}\else\togglefalse{t}\fi'}
    source = _merge_tex_files(files)
    assert not source.toggle_selection_safe
    source = pickle.loads(pickle.dumps(source.cut(0, len(source))))
    assert not source.toggle_selection_safe
    assert [c.latex for c in parse_tex_formulas(source)] == ['a', 'b']


def test_comments_and_verbatim_conditions_do_not_disable_known_toggle_selection():
    assert formulas('% \\ifnum0>1\n' + r'\verb|\ifcustom|\iftoggle{arxiv}{$bad$}{$x$}') == ['x']


def test_user_definition_shadows_builtin_toggle_command():
    assert formulas(r'\def\iftoggle#1#2#3{#3}\toggletrue{arxiv}'
                    r'\iftoggle{arxiv}{$bad$}{$x$}') == ['x']
    assert formulas(r'\def\toggletrue#1{}\toggletrue{arxiv}'
                    r'\iftoggle{arxiv}{$bad$}{$x$}') == ['x']


def test_protected_toggle_assignment_waits_for_edef_macro_call():
    assert formulas(r'\edef\later{\toggletrue{arxiv}}\iftoggle{arxiv}{$bad$}{$x$}'
                    r'\later\iftoggle{arxiv}{$y$}{$bad$}') == ['x', 'y']
    # The conditional itself is expandable, so its current branch is captured.
    assert formulas(r'\edef\captured{\iftoggle{arxiv}{bad}{x}}\toggletrue{arxiv}'
                    r'$\captured$') == ['x']


def test_literal_names_ignore_csname_whitespace():
    assert formulas(r'\toggletrue{ ar xiv }\iftoggle{arxiv}{$x$}{$bad$}') == ['x']


def test_selected_branch_keeps_original_lines_and_macro_call_origins_across_includes():
    files = {'main.tex': '\\documentclass{article}\n\\newtoggle{t}\n\\toggletrue{t}\n'
             '\\def\\choose{\\iftoggle{t}{$macro$}{$bad$}}\n\\begin{document}\n'
             '\\iftoggle{t}{\n\\input{eq}\n}{$bad$}\n\\choose\n$after$\n\\end{document}',
             'eq.tex': '% comment\n$x+y$\n\\iftoggle{t}{\n$z+w$\n}{$bad$}'}
    candidates = parse_tex_formulas(_merge_tex_files(files))
    assert [(c.latex, c.source_file, c.source_line, c.source_location_kind) for c in candidates] == [
        ('x+y', 'eq.tex', 2, 'literal'), ('z+w', 'eq.tex', 4, 'literal'),
        ('macro', 'main.tex', 9, 'macro'), ('after', 'main.tex', 10, 'literal')]


def test_traced_and_plain_expansion_agree_for_escaped_braces_and_empty_branches():
    source = r'\newtoggle{t}\iftoggle{t}{}{\{x\}}\toggletrue{t}\iftoggle{t}{}{$bad$}$z$'
    assert str(expand_macros(SourceText.file(source, 'main.tex'))) == expand_macros(source)
    assert formulas(source) == ['z']


def test_selected_recursion_and_work_are_bounded_and_unused_recursion_is_safe():
    source = r'\newtoggle{t}\def\loop{\iftoggle{t}{x}{\loop}}\loop'
    with pytest.raises(ExpansionLimitError, match='recursion'):
        expand_macros(source, max_depth=8)
    assert 'ok' in expand_macros(r'\newtoggle{t}\def\loop{\loop}\iftoggle{t}{\loop}{ok}')
    with pytest.raises(ExpansionLimitError, match='size'):
        expand_macros(r'\newtoggle{t}\iftoggle{t}{}{abcdefghij}', max_work_chars=10)


def test_reviewed_mamba_branch_occurrences():
    import hashlib
    import json
    import os
    from pathlib import Path
    from backend.extractor.arxiv_tex import _unpack_tex_source
    if os.environ.get('RUN_PAPER_AUDITS') != '1':
        pytest.skip('set RUN_PAPER_AUDITS=1 for pinned Mamba source')
    root = Path(__file__).resolve().parents[1]
    artifact = json.loads((root/'testdata/benchmarks/manifest.json').read_text())['papers']['2312.00752v2']['artifacts']['source']
    path = root/artifact['path']
    if not path.exists():
        pytest.skip('Pinned Mamba source is not downloaded')
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == artifact['sha256']
    candidates = parse_tex_formulas(_unpack_tex_source(data))
    inactive = {('src/background.tex', 79), ('src/background.tex', 80),
                ('src/method.tex', 290), ('src/experiments.tex', 29)}
    assert not any((c.source_file, c.source_line) in inactive for c in candidates)
    assert any(c.source_file == 'src/background.tex' and c.source_line == 71 for c in candidates)
    assert any(c.source_file == 'src/method.tex' and c.source_line == 281 for c in candidates)
    # The same file is included in the main text and an inactive appendix.
    assert sum(c.source_file == 'src/synthetic.tex' and c.source_line == 31 for c in candidates) == 2
    assert sum(c.source_file == 'src/benchmarks.tex' and c.source_line == 5 for c in candidates) == 1
