import gzip
import multiprocessing
import pickle
from concurrent.futures import ProcessPoolExecutor

import pytest
from httpx import ASGITransport, AsyncClient

from backend.extractor.arxiv_tex import _merge_tex_files, _unpack_tex_source, parse_tex_formulas
from backend.extractor.tex_macros import expand_macros
from backend.extractor.tex_provenance import SourceText
from backend.cache import load_candidates_cache, store_candidates_cache
from backend.matcher.index import build_index
from tests.test_source_limits import archive


def locations(candidates):
    return [(c.latex, c.source_file, c.source_line, c.source_location_kind) for c in candidates]


def test_includes_and_repeated_files_keep_original_lines_after_comments_and_preamble():
    files = {'paper/main.tex': '\\documentclass{article}\r\n% comment\r\n\\begin{document}\r\n'
             '$a+b$\\input{parts/equations}\r\n$u+v$\r\n\\input{parts/equations}\r\n\\end{document}',
             'paper/parts/equations.tex': '% ignored $q$\n\\begin{comment}\n$bad$\n\\end{comment}\n'
             '$x+y$\n\\input{tail}\n$z+w$',
             'paper/parts/tail.tex': '\\verb|$bad$|\n\\iffalse $bad$\\else\n\\[c+d\\]\\fi'}
    eqs = [('x+y', 'paper/parts/equations.tex', 5, 'literal'),
           ('c+d', 'paper/parts/tail.tex', 3, 'literal'),
           ('z+w', 'paper/parts/equations.tex', 7, 'literal')]
    assert locations(parse_tex_formulas(_merge_tex_files(files))) == [
        ('a+b', 'paper/main.tex', 4, 'literal'), *eqs,
        ('u+v', 'paper/main.tex', 5, 'literal'), *eqs]


def test_macro_generated_math_points_to_use_not_definition_or_generated_newlines():
    files = {'main.tex': '\\documentclass{article}\n\\input{macros}\n\\begin{document}\n'
             '\\formula{x}\n$\\value+z$\n\\outer\n\\ensuremath{a+b}\n$after$\n\\end{document}',
             'macros.tex': '\\newcommand{\\formula}[1]{\n\\[ #1+y \\]\n}\n'
             '\\def\\value{v}\n\\def\\outer{\\formula{w}}\n\\def\\unused{$notused$}'}
    assert locations(parse_tex_formulas(_merge_tex_files(files))) == [
        ('x+y', 'main.tex', 4, 'macro'), ('v+z', 'main.tex', 5, 'literal'),
        ('w+y', 'main.tex', 6, 'macro'), ('a+b', 'main.tex', 7, 'macro'),
        ('after', 'main.tex', 8, 'literal')]


def test_multiline_environment_start_and_macro_argument_origin_are_explicit():
    text = '\\newcommand{\\wrap}[1]{#1}\n\\begin{document}\n\\begin {equation}\n x+y=z\n\\end{equation}\n'
    text += '\\wrap{\n$a+b$\n}\n\\end{document}'
    assert locations(parse_tex_formulas(text, source_file='draft.tex')) == [
        ('x+y=z', 'draft.tex', 3, 'literal'), ('a+b', 'draft.tex', 6, 'macro')]


def test_no_filename_is_invented_for_single_gzip_or_plain_source():
    raw = b'% heading\n$x+y$'
    for text in [_unpack_tex_source(gzip.compress(raw)), raw.decode()]:
        assert locations(parse_tex_formulas(text)) == [('x+y', '', 2, 'literal')]


def test_archive_names_and_positions_survive_windows_spawn_and_pickle():
    source = _unpack_tex_source(archive([
        ('paper/main.tex', b'\\documentclass{article}\n\\begin{document}\\input{eq}\\end{document}'),
        ('paper/eq.tex', b'% first\n$x+y$')]))
    restored = pickle.loads(pickle.dumps(source))
    assert isinstance(restored, SourceText)
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context('spawn')) as pool:
        candidates = pool.submit(parse_tex_formulas, restored).result(timeout=30)
    assert locations(candidates) == [('x+y', 'paper/eq.tex', 2, 'literal')]


def test_slice_keeps_line_origin_and_macro_call_fixed_across_newlines():
    source = SourceText.file('heading\n\\def\\a{\n$x$\n$y$}\n\\a\n$z$', 'test.tex')
    expanded = expand_macros(source)
    for symbol, line, kind in [('x', 5, 'macro'), ('y', 5, 'macro'), ('z', 6, 'literal')]:
        offset = expanded.index('$' + symbol)
        assert expanded.location(offset) == ('test.tex', line, kind)
        assert expanded.cut(offset, len(expanded)).location(0) == ('test.tex', line, kind)


def test_plain_source_mapping_is_compressed_and_expansion_stays_identical():
    raw = ('prose $x+y$\n' * 10000) + '\\def\\a{x} $\\a+z$'
    traced = expand_macros(SourceText.file(raw, 'long.tex'))
    assert str(traced) == expand_macros(raw)
    assert len(traced.runs) < 10
    assert traced.location(traced.index('x+z')) == ('long.tex', 10001, 'macro')


def test_include_expansion_and_depth_are_bounded(monkeypatch):
    import backend.extractor.arxiv_tex as arxiv
    monkeypatch.setattr(arxiv, 'MAX_TEX_TOTAL_BYTES', 100)
    with pytest.raises(ValueError, match='processing limit'):
        _merge_tex_files({'main.tex': r'\documentclass{article}\input{eq}\input{eq}', 'eq.tex': 'x' * 60})
    monkeypatch.setattr(arxiv, 'MAX_TEX_TOTAL_BYTES', 100000)
    files = {f'{i}.tex': '\\input{' + str(i+1) + '}' for i in range(66)}
    files['0.tex'] = r'\documentclass{article}\input{1}'
    with pytest.raises(ValueError, match='nesting'):
        _merge_tex_files(files)


def test_empty_repeated_includes_have_a_work_limit(monkeypatch):
    import backend.extractor.arxiv_tex as arxiv
    monkeypatch.setattr(arxiv, 'MAX_TEX_INCLUDE_CALLS', 5)
    with pytest.raises(ValueError, match='include work'):
        _merge_tex_files({'main.tex': r'\documentclass{article}\input{branch}\input{branch}',
                          'branch.tex': r'\input{empty}\input{empty}', 'empty.tex': ''})


@pytest.mark.asyncio
async def test_cache_to_search_preserves_provenance_without_claiming_a_pdf_location():
    import backend.main as main
    candidates = parse_tex_formulas(_merge_tex_files({'main.tex': '% comment\n$x+y$\n$x+y$'}))
    store_candidates_cache('arxiv-tex', 'provenance', candidates)
    loaded = load_candidates_cache('arxiv-tex', 'provenance')
    assert locations(loaded) == locations(candidates)
    url = 'https://arxiv.org/pdf/1706.03762v7'
    main._sessions[url] = main.SessionState(index_cache=build_index(loaded), current_pdf_url=url, source_type='tex')
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/search', json={'url': url, 'query': 'x+y', 'mode': 'exact'})
    assert response.status_code == 200
    results = response.json()['matches']
    assert [(m['source_file'], m['source_line'], m['source_location_kind']) for m in results] == [
        ('main.tex', 2, 'literal'), ('main.tex', 3, 'literal')]
    assert all(m['page'] == -1 and m['bbox'] is None and m['location_status'] == 'unlocated' for m in results)
