from __future__ import annotations

import asyncio
import gzip
import io
import math
import posixpath
import re
import sys
import tarfile
from dataclasses import dataclass, field
from functools import lru_cache
from difflib import SequenceMatcher
from typing import Callable, List, Optional, Tuple

import fitz

try:  # Project dependency, but keep a safe fallback for unusual envs.
    from rapidfuzz import fuzz as _rapid_fuzz
except Exception:  # pragma: no cover
    _rapid_fuzz = None

from backend.extractor.text_layer import FormulaCandidate
from backend.extractor.tex_provenance import SourceBuilder, SourceText
from backend.pdf_geometry import displayed_geometry, unrotated_page_size
from backend.document_identity import arxiv_id
from backend.arxiv_versions import resolve_arxiv_id
from backend.artifacts import load_pdf_artifact, store_pdf_artifact
from backend.downloads import download_bytes
from backend.extractor.tex_syntax import delimited_math, mask_inactive_constants, mask_verbatim
from backend.extractor.tex_macros import expand_macros, read_group as _read_tex_group
from backend.extractor.tex_toggles import has_unsupported_condition

_SECTION_RE = re.compile(r'\\(?:sub)*section\*?\{([^}]+)\}')
_LABEL_RE = re.compile(r'\\label\{([^}]+)\}')
_COMMENT_ENV_RE = re.compile(r'\\begin\{comment\}.*?\\end\{comment\}', re.DOTALL)
_COMMENT_LINE_RE = re.compile(r'(?<!\\)%[^\n]*')

_DISPLAY_ENVS = [
    'equation', 'equation*', 'align', 'align*', 'gather', 'gather*',
    'multline', 'multline*', 'eqnarray', 'eqnarray*',
]

BBox = Tuple[float, float, float, float]

_GREEK_COMMANDS = {
    'alpha': 'alpha', 'beta': 'beta', 'gamma': 'gamma', 'delta': 'delta',
    'epsilon': 'epsilon', 'varepsilon': 'epsilon', 'zeta': 'zeta',
    'eta': 'eta', 'theta': 'theta', 'vartheta': 'theta', 'iota': 'iota',
    'kappa': 'kappa', 'lambda': 'lambda', 'mu': 'mu', 'nu': 'nu',
    'xi': 'xi', 'pi': 'pi', 'varpi': 'pi', 'rho': 'rho', 'varrho': 'rho',
    'sigma': 'sigma', 'varsigma': 'sigma', 'tau': 'tau', 'upsilon': 'upsilon',
    'phi': 'phi', 'varphi': 'phi', 'chi': 'chi', 'psi': 'psi', 'omega': 'omega',
    'Gamma': 'gamma', 'Delta': 'delta', 'Theta': 'theta', 'Lambda': 'lambda',
    'Xi': 'xi', 'Pi': 'pi', 'Sigma': 'sigma', 'Upsilon': 'upsilon',
    'Phi': 'phi', 'Psi': 'psi', 'Omega': 'omega',
}

_COMMAND_WORDS = {
    **_GREEK_COMMANDS,
    'sqrt': 'sqrt', 'sum': 'sum', 'prod': 'prod', 'int': 'int', 'iint': 'int',
    'iiint': 'int', 'oint': 'int', 'lim': 'lim', 'log': 'log', 'ln': 'ln',
    'exp': 'exp', 'sin': 'sin', 'cos': 'cos', 'tan': 'tan', 'max': 'max',
    'min': 'min', 'argmax': 'argmax', 'argmin': 'argmin', 'softmax': 'softmax',
    'det': 'det', 'dim': 'dim', 'Pr': 'pr', 'infty': 'infty', 'partial': 'partial',
    'nabla': 'nabla',
    # Formatting commands: keep their braced content by replacing only the command.
    'mathbb': '', 'mathcal': '', 'mathbf': '', 'boldsymbol': '', 'mathrm': '',
    'mathit': '', 'text': '', 'textrm': '', 'textnormal': '', 'textsc': '', 'texttt': '', 'operatorname': '', 'operatorname*': '', 'mathsf': '', 'mathtt': '', 'mathbbm': '', 'mathscr': '', 'mathfrak': '', 'bm': '', 'boldmath': '', 'ensuremath': '',
}

_DROP_COMMANDS = {
    'left', 'right', 'big', 'Big', 'bigg', 'Bigg', 'bigl', 'bigr', 'Bigl', 'Bigr',
    'biggl', 'biggr', 'Biggl', 'Biggr', 'quad', 'qquad', 'thinspace', 'enspace',
    'hspace', 'vspace', '!', ',', ';', ':', 'cdot', 'times', 'ldots', 'cdots',
    'dots', 'nonumber', 'notag', 'displaystyle', 'textstyle', 'scriptstyle',
    'scriptscriptstyle', 'label', 'ref', 'eqref', 'tag', 'color', 'textcolor',
    'begin', 'end', 'frac', 'limits', 'nolimits', 'overline', 'underline', 'hat', 'tilde',
    'bar', 'vec', 'dot', 'ddot', 'prime', 'mathop', 'mathrel', 'overset', 'underset', 'raisebox', 'tiny', 'scriptsize', 'small', 'normalsize', 'phantom', 'vphantom', 'hphantom', 'xspace', 'textvisiblespace',
}


# Extra command aliases used by newer ML/statistics papers. They improve both
# TeX-to-PDF matching and popup preview sanitization by turning presentational
# or relational macros into stable searchable tokens instead of prose words.
_COMMAND_WORDS.update({
    'ell': 'ell', 'leq': 'le', 'le': 'le', 'geq': 'ge', 'ge': 'ge',
    'neq': 'ne', 'ne': 'ne', 'approx': 'approx', 'sim': 'sim',
    'simeq': 'simeq', 'propto': 'propto', 'll': 'll', 'gg': 'gg',
    'to': 'to', 'rightarrow': 'to', 'leftarrow': 'to', 'mapsto': 'mapsto',
    'in': 'in', 'notin': 'notin', 'subset': 'subset', 'supset': 'supset',
    'subseteq': 'subseteq', 'supseteq': 'supseteq', 'forall': 'forall',
    'exists': 'exists', 'mid': 'mid', 'vert': 'vert', 'perp': 'perp',
    'langle': 'langle', 'rangle': 'rangle', 'top': 't', 'bot': 'bot',
    'mathbbm': '', 'mathscr': '', 'mathfrak': '', 'bm': '',
    'textbf': '', 'emph': '', 'ensuremath': '', 'widehat': '',
    'widetilde': '', 'widebar': '', 'overrightarrow': '',
})
_DROP_COMMANDS.update({
    'mathcolor', 'xspace', 'scalebox', 'kern', 'mkern', 'rlap', 'llap',
    'protect', 'small', 'scriptsize', 'normalsize', 'definecolor',
    'allowbreak', 'mbox', 'smash', 'strut', 'rule', 'hfill', 'vfill',
})

_UNICODE_WORDS = {
    'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'ε': 'epsilon', 'ϵ': 'epsilon',
    'ζ': 'zeta', 'η': 'eta', 'θ': 'theta', 'ϑ': 'theta', 'ι': 'iota', 'κ': 'kappa',
    'λ': 'lambda', 'μ': 'mu', 'µ': 'mu', 'ν': 'nu', 'ξ': 'xi', 'π': 'pi',
    'ρ': 'rho', 'σ': 'sigma', 'ς': 'sigma', 'τ': 'tau', 'υ': 'upsilon',
    'φ': 'phi', 'ϕ': 'phi', 'χ': 'chi', 'ψ': 'psi', 'ω': 'omega',
    'Α': 'alpha', 'Β': 'beta', 'Γ': 'gamma', 'Δ': 'delta', 'Ε': 'epsilon',
    'Ζ': 'zeta', 'Η': 'eta', 'Θ': 'theta', 'Ι': 'iota', 'Κ': 'kappa',
    'Λ': 'lambda', 'Μ': 'mu', 'Ν': 'nu', 'Ξ': 'xi', 'Π': 'pi', 'Ρ': 'rho',
    'Σ': 'sigma', 'Τ': 'tau', 'Υ': 'upsilon', 'Φ': 'phi', 'Χ': 'chi',
    'Ψ': 'psi', 'Ω': 'omega',
    '√': 'sqrt', '∑': 'sum', '∏': 'prod', '∫': 'int', '∞': 'infty',
    '≤': 'le', '≥': 'ge', '≠': 'ne', '≈': 'approx', '∈': 'in', '∉': 'notin',
    '∂': 'partial', '∇': 'nabla', '⊤': 't', 'ᵀ': 't', 'ᵗ': 't',
}

_SUP_SUB_TRANSLATION = str.maketrans({
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4', '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4', '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
    'ⁱ': 'i', 'ʲ': 'j', 'ⁿ': 'n', 'ᵏ': 'k', 'ₐ': 'a', 'ₑ': 'e', 'ₕ': 'h', 'ᵢ': 'i', 'ⱼ': 'j', 'ₖ': 'k',
    'ₗ': 'l', 'ₘ': 'm', 'ₙ': 'n', 'ₒ': 'o', 'ₚ': 'p', 'ᵣ': 'r', 'ₛ': 's', 'ₜ': 't', 'ᵤ': 'u', 'ᵥ': 'v', 'ₓ': 'x',
})

_MATH_SIGNAL_RE = re.compile(
    r'[=+−*/^_√∑∏∫∂≤≥≠≈∞α-ωΑ-Ωˆ˜∝∼∥⊂⊆⊃⊇∃∀∧∨⋅·×÷±∓→←↔]|\bO\s*\(|\b(?:sqrt|softmax|argmax|argmin|log|exp|sin|cos|tan)\b',
    re.I,
)


@dataclass
class _PdfLine:
    page: int
    text: str
    bbox: BBox
    page_w: float
    page_h: float
    norm: str
    norm_chars: list[tuple[str, BBox]]
    tokens: set[str]
    is_bold: bool = False
    math_fraction: float = 0.0
    is_group: bool = False
    group_kind: str = ""
    line_index: int = -1
    nearby_text: str = ""
    raw_chars: list[tuple[str, BBox]] = field(default_factory=list)
    page_rotation: int = 0

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]


def is_arxiv_url(url: str) -> bool:
    return bool(arxiv_id(url))


def extract_arxiv_id(url: str) -> str:
    identifier = arxiv_id(url)
    if not identifier:
        raise ValueError(f"No arXiv ID in URL: {url}")
    return identifier


def _strip_commands(text: str) -> str:
    return re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text).strip()


async def _httpx_get(url: str) -> bytes:
    return await download_bytes(url)


async def fetch_tex_source(arxiv_id: str) -> str:
    content = await _httpx_get(f"https://arxiv.org/src/{arxiv_id}")
    from backend.processing import run_document_job
    return await run_document_job(_unpack_tex_source, content)


MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_TEX_FILE_BYTES = 16 * 1024 * 1024
MAX_TEX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SOURCE_MEMBERS = 20000
MAX_TEX_INCLUDE_CALLS = 20000


class _BoundedArchiveReader:
    def __init__(self, source):
        self.source, self.total = source, 0

    def read(self, size=-1):
        remaining = MAX_SOURCE_BYTES - self.total
        data = self.source.read(min(size, remaining + 1) if size >= 0 else remaining + 1)
        self.total += len(data)
        if self.total > MAX_SOURCE_BYTES:
            raise ValueError('Decompressed arXiv source exceeds the processing limit')
        return data


def _unpack_tex_source(content: bytes) -> str:
    anonymous_source = False
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as source, tarfile.open(fileobj=_BoundedArchiveReader(source), mode="r|") as tar:
            tex_files: dict[str, str] = {}
            total = 0
            for index, member in enumerate(tar):
                if index >= MAX_SOURCE_MEMBERS:
                    raise ValueError('arXiv source contains too many archive members')
                if member.isfile() and member.name.endswith(".tex"):
                    total += member.size
                    if member.size > MAX_TEX_FILE_BYTES or total > MAX_TEX_TOTAL_BYTES:
                        raise ValueError('TeX source exceeds the processing limit')
                    f = tar.extractfile(member)
                    if f:
                        tex_files[member.name] = f.read().decode("utf-8", errors="replace")
    except (tarfile.TarError, OSError, EOFError):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as source:
                tex_content = source.read(MAX_TEX_FILE_BYTES + 1)
            if len(tex_content) > MAX_TEX_FILE_BYTES:
                raise ValueError('TeX source exceeds the processing limit')
            tex_files = {"main.tex": tex_content.decode("utf-8", errors="replace")}
            anonymous_source = True
        except (OSError, EOFError):
            raise ValueError("Archive is not a tar.gz or gzipped .tex file")

    if not tex_files:
        raise ValueError("no TeX files found")

    # A bare gzip has no archive member name. Do not invent a main.tex citation.
    if anonymous_source:
        return SourceText.file(_preprocess(tex_files['main.tex']), '')
    return _merge_tex_files(tex_files)



def _strip_comments_preserve_newlines(tex: str) -> str:
    r"""Remove TeX comments before expanding \input or \include commands."""
    def env_repl(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    tex = _COMMENT_ENV_RE.sub(env_repl, tex or "")
    out: list[str] = []
    for line in tex.splitlines(keepends=True):
        cut = len(line)
        pos = 0
        while True:
            pct = line.find("%", pos)
            if pct < 0:
                break
            slash_count = 0
            idx = pct - 1
            while idx >= 0 and line[idx] == "\\":
                slash_count += 1
                idx -= 1
            if slash_count % 2 == 0:
                cut = pct
                break
            pos = pct + 1
        if cut < len(line):
            newline = "\n" if line.endswith("\n") else ""
            out.append(line[:cut] + newline)
        else:
            out.append(line)
    return "".join(out)


def _document_body(tex: str) -> str:
    begin = re.search(r'\\begin\{document\}', tex)
    if not begin:
        return tex
    end = re.search(r'\\end\{document\}', tex[begin.end():])
    stop = begin.end() + end.start() if end else len(tex)
    return tex.cut(begin.end(), stop) if isinstance(tex, SourceText) else tex[begin.end():stop]


def _find_root(tex_files: dict) -> str:
    candidates = [n for n, c in tex_files.items() if r'\documentclass' in c]
    if not candidates:
        return max(tex_files, key=lambda n: len(tex_files[n]))
    with_doc = [n for n in candidates if r'\begin{document}' in tex_files[n]]
    return (with_doc or candidates)[0]


def _merge_tex_files(tex_files: dict) -> str:
    tex_files = {posixpath.normpath(name.replace('\\', '/')): text for name, text in tex_files.items()}
    if not tex_files:
        return ''
    root = _find_root(tex_files)
    root_dir = posixpath.dirname(root)
    prepared_files = {}
    include_calls, work_chars = 0, 0

    def inline(filename: str, stack: set) -> str:
        nonlocal include_calls, work_chars
        # Strip comments before expanding inputs. Otherwise commented-out draft
        # appendices are parsed as if they were part of the PDF.
        if len(stack) > 64:
            raise ValueError('TeX include nesting exceeds the processing limit')
        include_calls += 1
        if include_calls > MAX_TEX_INCLUDE_CALLS:
            raise ValueError('TeX include work exceeds the processing limit')
        # Preprocessing preserves newlines, so these are original file lines.
        if filename not in prepared_files:
            prepared_files[filename] = SourceText.file(_preprocess(tex_files[filename]), filename)
        content = prepared_files[filename]
        work_chars += len(content)
        if work_chars > MAX_TEX_TOTAL_BYTES:
            raise ValueError('TeX include work exceeds the processing limit')

        def replacer(m):
            fname = (m.group(1) or m.group(2)).strip()
            if not fname.endswith('.tex'):
                fname += '.tex'
            choices = [posixpath.join(root_dir, fname), posixpath.join(posixpath.dirname(filename), fname), fname]
            basename_matches = [name for name in tex_files if posixpath.basename(name) == posixpath.basename(fname)]
            if len(basename_matches) == 1:
                choices += basename_matches
            for candidate in map(posixpath.normpath, choices):
                if candidate in tex_files and candidate not in stack:
                    return inline(candidate, stack | {candidate})
            return ''
        builder = SourceBuilder(max_chars=MAX_TEX_TOTAL_BYTES)
        last = 0
        for match in re.finditer(r'\\(?:input|include)\s*(?:\{([^}]+)\}|([^\s{}%]+))', content):
            builder.append_source(content, last, match.start())
            included = replacer(match)
            if included:
                builder.append_source(included)
            last = match.end()
        builder.append_source(content, last)
        return builder.finish()

    return inline(root, {root})


def _preprocess(tex: str) -> str:
    prepared = _strip_comments_preserve_newlines(mask_verbatim(tex))
    return SourceText(mask_inactive_constants(prepared),
                      toggle_selection_safe=not has_unsupported_condition(prepared))


def _section_map(tex: str) -> list:
    """Return rendered section headings with balanced TeX groups.

    The old one-regex version stopped at the first closing brace. Headings that
    contained "\\label{...}" or formatting wrappers were truncated, which in
    turn let formulas be assigned to the wrong PDF section/page.
    """
    sections: list[tuple[int, str]] = []
    for m in re.finditer(r'\\(?:section|subsection|subsubsection)\*?\s*\{', tex):
        body, _ = _read_tex_group(tex, m.end() - 1)
        if body is None:
            continue
        body = _LABEL_RE.sub('', body)
        sections.append((m.start(), _strip_commands(body)))
    return sections


def _section_at(pos: int, sections: list) -> str:
    result = ""
    for spos, stext in sections:
        if spos <= pos:
            result = stext
        else:
            break
    return result



def _context_before_after(tex: str, start: int, end: int, limit: int = 420) -> tuple[str, str]:
    before = tex[max(0, start - limit):start]
    after = tex[end:min(len(tex), end + limit)]
    return before, after


def _make_tex_candidate(
    *,
    tex: str,
    start: int,
    end: int,
    latex: str,
    section: str,
    env: str,
    label: str = "",
) -> FormulaCandidate:
    before, after = _context_before_after(tex, start, end)
    filename, line, kind = tex.location(start) if isinstance(tex, SourceText) else ('', 0, '')
    return FormulaCandidate(
        latex=latex,
        source="tex",
        label=label,
        section=section,
        env=env,
        tex_context_before=before,
        tex_context_after=after,
        source_file=filename,
        source_line=line,
        source_location_kind=kind,
    )



def _formula_body_worth_indexing(body: str) -> bool:
    body = (body or '').strip()
    if not body:
        return False
    # Explicit math delimiters provide evidence for one-letter symbols. Drop
    # punctuation, standalone numeric debris and author/reference superscripts.
    if re.fullmatch(r'[.,;:!?~\s]+', body):
        return False
    if re.fullmatch(r'(?:\\[,;!:]|\\quad|\\qquad|\\!|\\to|\\rightarrow|\\leftarrow|\\mapsto|\s)+', body):
        return False
    if re.fullmatch(r'\^\s*\{?\s*\d+\s*\}?', body):
        return False
    if re.fullmatch(r'\d+(?:\.\d+)?\s*(?:\\%)?', body):
        return False
    compact = _norm_string(body, is_latex=True)
    if not compact or (len(compact) == 1 and not compact.isalpha()):
        return False
    # Pure wrappers/macros with no surviving math token are usually layout
    # commands or author/list markers, not searchable formulas.
    if not re.search(r'[A-Za-z0-9α-ωΑ-Ω]', compact):
        return False
    return True


def parse_tex_formulas(tex: str, *, source_file: str = '') -> List[FormulaCandidate]:
    # Merged SourceText has already been preprocessed file by file. Repeating
    # preprocessing after concatenation can change comment/conditional scope.
    preprocessed = tex if isinstance(tex, SourceText) else SourceText.file(_preprocess(tex), source_file)
    tex = _document_body(expand_macros(preprocessed))
    sections = _section_map(tex)
    found: list[tuple[int, FormulaCandidate]] = []

    occupied: list[tuple[int, int]] = []

    def overlaps_existing(a: int, b: int) -> bool:
        return any(not (b <= x or a >= y) for x, y in occupied)

    for env in _DISPLAY_ENVS:
        pat = re.compile(
            r'\\begin\{' + re.escape(env) + r'\}(.*?)\\end\{' + re.escape(env) + r'\}',
            re.DOTALL,
        )
        for m in pat.finditer(tex):
            body = m.group(1)
            label = ""
            lm = _LABEL_RE.search(body)
            if lm:
                label = lm.group(1)
                body = _LABEL_RE.sub('', body, count=1)
            latex = body.strip()
            if latex:
                found.append((m.start(), _make_tex_candidate(
                    tex=tex,
                    start=m.start(),
                    end=m.end(),
                    latex=latex,
                    label=label,
                    section=_section_at(m.start(), sections),
                    env=env.rstrip('*'),
                )))
                occupied.append((m.start(), m.end()))

    for start, end, body, env in delimited_math(tex, occupied):
        label = ""
        lm = _LABEL_RE.search(body)
        if lm:
            label = lm.group(1)
            body = _LABEL_RE.sub('', body, count=1)
        body = body.strip()
        if body and (env == "display" or _formula_body_worth_indexing(body)):
            found.append((start, _make_tex_candidate(
                tex=tex, start=start, end=end, latex=body, label=label,
                section=_section_at(start, sections), env=env,
            )))

    found.sort(key=lambda x: x[0])
    results = [fc for _, fc in found]
    for idx, candidate in enumerate(results):
        candidate.source_order = idx
    return results


async def _fetch_pdf_bytes(arxiv_id: str) -> bytes:
    return await _httpx_get(f"https://arxiv.org/pdf/{arxiv_id}")


def _union_bbox(boxes: list[BBox]) -> BBox:
    x0s, y0s, x1s, y1s = zip(*boxes)
    return (
        float(min(x0s)),
        float(min(y0s)),
        float(max(x1s)),
        float(max(y1s)),
    )


@lru_cache(maxsize=4096)
def _word_for_char(ch: str) -> str:
    if not ch:
        return ''
    ch = ch.translate(_SUP_SUB_TRANSLATION)
    if ch in _UNICODE_WORDS:
        return _UNICODE_WORDS[ch]
    if ch.isalnum():
        return ch.lower()
    return ''


def _flatten_frac_commands(text: str) -> str:
    """Replace nested \frac{a}{b} with its rendered text-bearing parts."""
    pattern = r'\frac'
    while True:
        start = text.find(pattern)
        if start < 0:
            return text
        pos = start + len(pattern)
        while pos < len(text) and text[pos].isspace():
            pos += 1
        numerator, after_num = _read_tex_group(text, pos)
        if numerator is None:
            text = text[:start] + ' ' + text[pos:]
            continue
        pos = after_num
        while pos < len(text) and text[pos].isspace():
            pos += 1
        denominator, after_den = _read_tex_group(text, pos)
        if denominator is None:
            text = text[:start] + f' {numerator} ' + text[after_num:]
            continue
        text = text[:start] + f' {numerator} {denominator} ' + text[after_den:]



@lru_cache(maxsize=32768)
def _latex_to_match_text(tex: str) -> str:
    s = tex or ''
    s = _LABEL_RE.sub(' ', s)
    s = re.sub(r'\\(?:begin|end|tag|ref|eqref)\{[^}]*\}', ' ', s)
    # Presentation wrappers often survive macro expansion in arXiv sources.
    # Keep their mathematical payload and discard only the styling argument.
    for _ in range(4):
        new_s = re.sub(r'\\(?:mathcolor|textcolor|mcol)\s*\{[^{}]*\}\s*\{([^{}]*)\}', r' \1 ', s)
        new_s = re.sub(r'\\(?:ensuremath|bm|boldsymbol|mathbf|mathrm|mathit|mathsf|mathtt|mathbb|mathbbm|mathcal|mathscr|mathfrak|text|textrm|textnormal|operatorname\*?|emph|textbf)\s*\{([^{}]*)\}', r' \1 ', new_s)
        if new_s == s:
            break
        s = new_s

    # Flatten fractions before the generic command replacement. The denominator
    # often contains nested commands such as \sqrt{d_k}; a simple regex leaves a
    # literal "frac" token in the match key, which then causes the bbox matcher
    # to lock on only the first part of the formula.
    for _ in range(6):
        flattened = _flatten_frac_commands(s)
        if flattened == s:
            break
        s = flattened

    def repl(m: re.Match[str]) -> str:
        cmd = m.group(1)
        if cmd in _COMMAND_WORDS:
            return ' ' + _COMMAND_WORDS[cmd] + ' '
        if cmd in _DROP_COMMANDS:
            return ' '
        return ' ' + cmd + ' '

    s = re.sub(r'\\([A-Za-z]+\*?|.)', repl, s)
    s = s.replace('{', ' ').replace('}', ' ')
    return s


@lru_cache(maxsize=131072)
def _norm_string(text: str, *, is_latex: bool = False) -> str:
    if is_latex:
        text = _latex_to_match_text(text)
    out: list[str] = []
    for ch in text:
        out.append(_word_for_char(ch))
    return ''.join(out)


def _norm_chars_from_text(text: str, boxes: list[BBox], *, break_on_space: bool = False) -> list[tuple[str, BBox]]:
    result: list[tuple[str, BBox]] = []
    for idx, ch in enumerate(text):
        box = boxes[idx] if idx < len(boxes) else (0.0, 0.0, 0.0, 0.0)
        if break_on_space and ch.isspace():
            # Sentinel has no bbox and prevents short math queries from matching
            # by gluing the end of one prose word to the beginning of the next
            # (e.g. query d_k matching "weighted keys" as d + k).
            if result and result[-1][0] != '|':
                result.append(('|', (0.0, 0.0, 0.0, 0.0)))
            continue
        word = _word_for_char(ch)
        for out_ch in word:
            result.append((out_ch, box))
    return result


def _tokens_for_matching(text: str, *, is_latex: bool = False) -> set[str]:
    normalized = _latex_to_match_text(text) if is_latex else text
    tokens: set[str] = set()
    for raw in re.findall(r'[A-Za-z0-9]+|[α-ωΑ-Ω√∑∏∫∞≤≥≠≈∂∇]', normalized):
        token = _norm_string(raw)
        if len(token) >= 2:
            tokens.add(token)
    compact = _norm_string(text, is_latex=is_latex)
    if len(compact) >= 2:
        tokens.add(compact)
    return tokens



@lru_cache(maxsize=65536)
def _word_tokens_for_context(text: str, *, is_latex: bool = False) -> tuple[str, ...]:
    text = _latex_to_match_text(text) if is_latex else text
    tokens: list[str] = []
    for raw in re.findall(r'[A-Za-z0-9]+|[α-ωΑ-Ω√∑∏∫∞≤≥≠≈∂∇]', text):
        norm = _norm_string(raw)
        if len(norm) >= 2:
            tokens.append(norm)
    return tuple(tokens)


def _is_float_or_table_candidate(candidate: FormulaCandidate) -> bool:
    if candidate.env != "inline":
        return False
    before = getattr(candidate, 'tex_context_before', '')
    after = getattr(candidate, 'tex_context_after', '')
    ctx = before[-1200:] + getattr(candidate, 'latex', '') + after[:1200]
    if re.search(r'\\begin\{(?:table|tabular|array|figure)\*?\}', ctx):
        return True
    # Tabular rows often expose only immediate row separators around each inline
    # math cell. Keep this local so a preceding align environment does not make
    # normal prose formulas ignore their section region.
    local = before[-140:] + getattr(candidate, 'latex', '') + after[:140]
    return '&' in local and '\\' in local and not re.search(
        r'\\(?:begin|end)\{(?:align|align\*|equation|equation\*|split|gather)\}',
        local,
    )


def _candidate_context_tokens(candidate: FormulaCandidate) -> tuple[list[str], list[str]]:
    cached = getattr(candidate, '_formula_search_context_tokens', None)
    if cached is not None:
        return cached
    before = _word_tokens_for_context(getattr(candidate, 'tex_context_before', ''), is_latex=True)[-10:]
    after = _word_tokens_for_context(getattr(candidate, 'tex_context_after', ''), is_latex=True)[:10]
    cached = (before, after)
    setattr(candidate, '_formula_search_context_tokens', cached)
    return cached


def _line_context_token_set(line: _PdfLine) -> set[str]:
    cached = getattr(line, '_formula_search_context_tokens', None)
    if cached is not None:
        return cached
    cached = set(_word_tokens_for_context(line.nearby_text or line.text))
    setattr(line, '_formula_search_context_tokens', cached)
    return cached


def _context_score(candidate: FormulaCandidate, line: _PdfLine) -> float:
    before, after = _candidate_context_tokens(candidate)
    if not before and not after:
        return 0.0
    pdf_words = _line_context_token_set(line)
    if not pdf_words:
        return 0.0
    before_score = sum(1 for token in before if token in pdf_words) / max(1, len(before))
    after_score = sum(1 for token in after if token in pdf_words) / max(1, len(after))
    return 0.5 * before_score + 0.5 * after_score


def _inline_prose_anchor(candidate: FormulaCandidate, tex_norm: str) -> Optional[tuple[str, str, str]]:
    """Require two immediate prose words on each side of a short formula.

    Nearby bag-of-words context cannot distinguish two adjacent sentences that
    repeat the same symbol. This stronger, ordered anchor only breaks that tie
    when both sides and the entire formula occur together on a rendered line.
    TeX commands, math delimiters and punctuation between words disable it.
    """
    # Single letters need font/word-boundary evidence before a flattened text
    # anchor can safely override their existing ranking.
    if candidate.env != 'inline' or not 3 <= len(tex_norm) <= 8:
        return None
    before = re.search(r'(?<![A-Za-z\\])([A-Za-z]{2,})\s+([A-Za-z]{2,})\s*$',
                       candidate.tex_context_before)
    after = re.match(r'\s*([A-Za-z]{2,})\s+([A-Za-z]{2,})\b', candidate.tex_context_after)
    if before is None or after is None:
        return None
    return (''.join(before.groups()).lower(), tex_norm, ''.join(after.groups()).lower())


def _line_prose_anchor_bbox(anchor: Optional[tuple[str, str, str]], line: _PdfLine) -> Optional[BBox]:
    # PDF accent glyphs have no counterpart in the TeX matching key (\hat and
    # \tilde are presentation commands there). Only this contextual comparison
    # ignores them; character coordinates and query highlights remain intact.
    if anchor is None:
        return None
    prefix, formula, suffix = anchor
    text = line.norm.replace('|', '').replace('ˆ', '').replace('˜', '')
    needle = prefix + formula + suffix
    start = text.find(needle)
    if start < 0 or text.find(needle, start + 1) >= 0:
        return None
    positions = [i for i, (char, _) in enumerate(line.norm_chars) if char not in '|ˆ˜']
    if text != ''.join(line.norm_chars[i][0] for i in positions):
        return None
    # Include discarded accent glyphs between the preceding prose and formula;
    # retain the original character boxes so repeats on one line stay distinct.
    first = positions[start + len(prefix) - 1] + 1
    end = positions[start + len(prefix) + len(formula)]
    boxes = [box for _, box in line.norm_chars[first:end]
             if box[2] > box[0] and box[3] > box[1]]
    if not boxes:
        return None
    bounds = _union_bbox(boxes)
    unique_boxes = set(boxes)
    glyph_width = sum(box[2]-box[0] for box in unique_boxes)
    glyph_height = max(box[3]-box[1] for box in unique_boxes)
    # Row groups can interleave numerators/denominators from separate inline
    # fractions. Ordered text is insufficient if its glyphs span distant places.
    if (bounds[2]-bounds[0] > glyph_width + max(12.0, 3*glyph_height)
            or bounds[3]-bounds[1] > 3*glyph_height):
        return None
    # Normalization drops operators and parentheses. The two prose boundary
    # glyphs also identify the full intervening raw span, including punctuation.
    left_box, right_box = line.norm_chars[first-1][1], line.norm_chars[end][1]
    left = [i for i, (_, box) in enumerate(line.raw_chars) if box == left_box]
    right = [i for i, (_, box) in enumerate(line.raw_chars) if box == right_box]
    if len(left) == len(right) == 1 and left[0] < right[0]:
        raw = line.raw_chars[left[0]+1:right[0]]
        raw_key = _norm_string(''.join(char for char, _ in raw)).replace('ˆ', '').replace('˜', '')
        raw_boxes = [box for char, box in raw
                     if not char.isspace() and box[2] > box[0] and box[3] > box[1]]
        if raw_key == formula and raw_boxes:
            raw_bounds = _union_bbox(raw_boxes)
            # Some embedded minus fonts report a descent into the next prose
            # line. Retain the math glyph band instead of enlarging it that far.
            height = bounds[3] - bounds[1]
            if raw_bounds[3]-raw_bounds[1] <= max(1.5*height, height+4.0):
                return raw_bounds
    return bounds


def _partial_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b:
        # The TeX-derived formula key appears inside the PDF line. This is the
        # strong containment case we want for rendered equations and inline math.
        shorter = len(a)
        longer = len(b)
        return 1.0 if shorter >= 3 else min(0.92, shorter / max(1, longer) + 0.25)
    if b in a:
        # A short PDF line such as a section title or a function name can appear
        # inside a much longer formula key (e.g. "attention" inside
        # Attention(Q,K,V)=softmax(...)). Treat that as weak evidence, otherwise
        # the bbox mapper snaps to prose/headings instead of the equation line.
        coverage = len(b) / max(1, len(a))
        if len(b) < 3:
            return min(0.35, coverage + 0.08)
        return min(0.58, 0.16 + 0.66 * coverage)
    # Very short normalized math keys are common and visually similar
    # (O(n), O(n/r), d_k, d_v). Fuzzy partial matching makes unrelated short
    # formulas look too similar, so require real containment / token overlap for
    # high scores and cap the non-containing fallback.
    if min(len(a), len(b)) <= 4:
        # Short non-containing keys are extremely ambiguous and cannot safely
        # prove a PDF location. Avoid SequenceMatcher here: it was both slow
        # and a source of weak false positives for symbols like d_k / O(1).
        common = len(set(a) & set(b))
        return min(0.36, (2.0 * common / max(1, len(set(a)) + len(set(b)))) * 0.42)
    if _rapid_fuzz is not None:
        return float(_rapid_fuzz.partial_ratio(a, b)) / 100.0
    return SequenceMatcher(None, a, b).ratio()


@lru_cache(maxsize=65536)
def _looks_like_pdf_math_line(text: str) -> bool:
    return bool(text and _MATH_SIGNAL_RE.search(text))


def _similarity_score(tex_norm: str, tex_tokens: set[str], line: _PdfLine, candidate: Optional[FormulaCandidate] = None, *, ctx: Optional[float] = None) -> float:
    if not tex_norm or not line.norm:
        return 0.0
    partial = _partial_ratio(tex_norm, line.norm)
    overlap = tex_tokens & line.tokens
    token_recall = len(overlap) / max(1, len(tex_tokens))
    token_precision = len(overlap) / max(1, len(line.tokens))
    token_f1 = 0.0 if not overlap else (2 * token_recall * token_precision) / max(0.001, token_recall + token_precision)
    score = max(partial, 0.72 * token_recall + 0.28 * token_f1)
    if tex_norm == line.norm or tex_norm in line.tokens:
        score += 0.14
    elif len(tex_norm) <= 8 and tex_norm not in line.norm and not overlap:
        score -= 0.12
    if candidate is not None and candidate.env == "inline":
        if ctx is None:
            ctx = _context_score(candidate, line)
        # Very short inline formulas like d_k, h, or N=6 appear many times.
        # Matching them by the formula text alone is ambiguous, so surrounding
        # prose must dominate the score.
        if len(tex_norm) <= 4 and (tex_norm in line.norm or token_recall > 0):
            if tex_norm == line.norm or tex_norm in line.tokens:
                score = max(score, 0.80 + 0.14 * ctx + 0.06 * token_f1)
            else:
                score = max(score * 0.35, 0.48 + 0.45 * ctx + 0.07 * token_f1)
        else:
            score += 0.18 * ctx
    line_has_math = _looks_like_pdf_math_line(line.text)
    if line_has_math:
        score += 0.04
    else:
        # Prose lines can share a word with \mathrm{...} or \text{...}
        # fragments. Penalize them unless they almost completely explain the
        # TeX formula and the line itself.
        if token_recall < 0.72 or token_precision < 0.72:
            score -= 0.18
    if len(line.text) > 180 and token_recall < 0.8:
        score -= 0.08
    return max(0.0, min(1.0, score))


@lru_cache(maxsize=65536)
def _normalize_section_key(text: str) -> str:
    s = _strip_commands(text or '').lower()
    s = re.sub(r'^\s*\d+(?:\.\d+)*\.?\s*', '', s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _section_line_matches(section_key: str, line_text: str) -> bool:
    if not section_key:
        return False
    line_key = _normalize_section_key(line_text)
    if not line_key:
        return False
    return line_key == section_key



@lru_cache(maxsize=1024)
def _font_is_bold(font_name: str, flags: int) -> bool:
    name = (font_name or '').lower()
    return bool((flags & 16) or 'bold' in name or 'medi' in name or 'cmbx' in name)


@lru_cache(maxsize=1024)
def _font_is_math(font_name: str) -> bool:
    name = (font_name or '').lower()
    return any(marker in name for marker in (
        'cmmi', 'cmsy', 'cmex', 'cmmib', 'msam', 'msbm', 'cmbsy', 'math',
    ))


def _set_page_line_context(page_lines: list[_PdfLine]) -> None:
    """Attach context to lines already sorted by y, x and extraction order.

    A sliding window visits only peers within the existing 3.5-point baseline
    tolerance. Comparing differences (rather than rounded y +/- tolerance
    bounds) preserves the original inclusive floating-point boundary. Unusual
    non-finite coordinates retain the original scan behavior.
    """
    ys = [line.y0 for line in page_lines]
    finite = all(math.isfinite(y) for y in ys)
    left = right = 0
    for idx, line in enumerate(page_lines):
        if finite:
            y = ys[idx]
            while left < len(ys) and y - ys[left] > 3.5:
                left += 1
            while right < len(ys) and ys[right] - y <= 3.5:
                right += 1
            same_row = page_lines[left:right]
        else:
            same_row = [other for other in page_lines if abs(other.y0 - line.y0) <= 3.5]
        pieces = [page_lines[idx - 1].text] if idx > 0 else []
        # Table cells arrive as separate lines: retain their left-to-right row
        # labels, stable ties and the preceding/following line context.
        pieces.extend(other.text for other in sorted(same_row, key=lambda item: item.x0))
        if idx + 1 < len(page_lines):
            pieces.append(page_lines[idx + 1].text)
        line.nearby_text = " ".join(dict.fromkeys(pieces))


def _extract_pdf_lines(doc: fitz.Document) -> list[_PdfLine]:
    lines: list[_PdfLine] = []
    line_index = 0
    for page_num, page in enumerate(doc):
        page_w, page_h = unrotated_page_size(page)
        try:
            # TEXTFLAGS_TEXT omits TEXT_PRESERVE_IMAGES -- the one difference
            # from the plain 'rawdict' default. We only ever read type==0
            # (text) blocks below, never the image blocks that flag adds, but
            # PyMuPDF still pays to decode/attach that image data unless it's
            # told not to. On image-heavy PDFs (figures, qualitative-result
            # pages) that's the difference between ~2.7s and ~0.12s per
            # document for this call alone -- verified byte-for-byte identical
            # char/span data (text, bbox, font, flags) with the default flags
            # across every bundled paper before adopting this.
            raw = page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)
        except Exception:
            raw = page.get_text('dict', flags=fitz.TEXTFLAGS_TEXT)

        for block in raw.get('blocks', []):
            if block.get('type') != 0:
                continue
            for line in block.get('lines', []):
                chars: list[str] = []
                boxes: list[BBox] = []
                span_boxes: list[BBox] = []
                bold = False
                math_chars = 0
                total_nonspace_chars = 0
                for span in line.get('spans', []):
                    raw_span_box = span.get('bbox', (0, 0, 0, 0))
                    span_bbox = tuple(map(float, raw_span_box))
                    if len(span_bbox) == 4:
                        span_boxes.append(span_bbox)  # type: ignore[arg-type]
                    font_name = str(span.get('font', '') or '')
                    flags = int(span.get('flags', 0) or 0)
                    bold = bold or _font_is_bold(font_name, flags)
                    span_text_chars: list[str] = []
                    if span.get('chars'):
                        for ch in span.get('chars', []):
                            c = str(ch.get('c', '') or '')
                            span_text_chars.append(c)
                            bbox_raw = ch.get('bbox', span_bbox)
                            bbox = tuple(map(float, bbox_raw))
                            if len(bbox) != 4:
                                bbox = span_bbox
                            chars.append(c)
                            boxes.append(bbox)  # type: ignore[arg-type]
                    else:
                        text = str(span.get('text', '') or '')
                        for c in text:
                            span_text_chars.append(c)
                            chars.append(c)
                            boxes.append(span_bbox)  # type: ignore[arg-type]
                    nonspace = sum(1 for c in span_text_chars if not c.isspace())
                    total_nonspace_chars += nonspace
                    if _font_is_math(font_name):
                        math_chars += nonspace

                text_full = ''.join(chars)
                text = text_full.strip()
                if not text:
                    continue
                bbox = _union_bbox(boxes or span_boxes)
                line_math_fraction = math_chars / max(1, total_nonspace_chars)
                line_has_math_signal = _looks_like_pdf_math_line(text_full)
                # Preserve prose word boundaries in normalization. On non-math
                # lines this blocks false spans like d_k -> "weighted keys".
                break_on_space = line_math_fraction <= 0.0 and not line_has_math_signal
                norm_chars = _norm_chars_from_text(text_full, boxes, break_on_space=break_on_space)
                norm = ''.join(c for c, _ in norm_chars)
                tokens = _tokens_for_matching(text_full)
                if not norm:
                    continue
                lines.append(_PdfLine(
                    page=page_num,
                    text=text,
                    bbox=bbox,
                    page_w=page_w,
                    page_h=page_h,
                    page_rotation=page.rotation,
                    norm=norm,
                    norm_chars=norm_chars,
                    raw_chars=[(glyph, box) for char, box in zip(chars, boxes) for glyph in char],
                    tokens=tokens,
                    is_bold=bold,
                    math_fraction=line_math_fraction,
                    is_group=False,
                    line_index=line_index,
                ))
                line_index += 1
    lines.sort(key=lambda line: (line.page, line.y0, line.x0, line.line_index))
    lines_by_page: dict[int, list[_PdfLine]] = {}
    for idx, line in enumerate(lines):
        line.line_index = idx
        lines_by_page.setdefault(line.page, []).append(line)
    for page_lines in lines_by_page.values():
        _set_page_line_context(page_lines)
    return lines



def _has_numbering_peer(line: _PdfLine, lines: list[_PdfLine]) -> bool:
    for other in lines:
        if other is line or other.page != line.page:
            continue
        if abs(other.y0 - line.y0) > 1.2:
            continue
        if other.x1 > line.x0 + 2:
            continue
        if re.fullmatch(r'\d+(?:\.\d+)*', other.text.strip()):
            return True
    return False


def _contents_heading_ids(lines: list[_PdfLine], headings: list[tuple[str, _PdfLine]]) -> set[int]:
    """Recognize split contents rows by their right-hand page references.

    Bold text and a section number also occur in a contents list. Require a
    contents title above the row, or at least three distinct headings whose
    page references share a right edge (an unlabelled continuation page).
    Exclude individual rows, so a real section below a contents list survives.
    """
    pages: dict[int, list[_PdfLine]] = {}
    for line in lines:
        pages.setdefault(line.page, []).append(line)
    rows: dict[int, list[tuple[str, _PdfLine, float]]] = {}
    for key, line in headings:
        references = [other for other in pages[line.page]
                      if other.x0 >= line.x1 + 2 and abs(other.y0 - line.y0) <= 1.5
                      and re.fullmatch(r'(?:\d{1,4}|[ivxlcdmIVXLCDM]+)', other.text.strip())]
        if references:
            rows.setdefault(line.page, []).append((key, line, max(other.x1 for other in references)))
    excluded: set[int] = set()
    for page, candidates in rows.items():
        titles = [line.y0 for line in pages[page]
                  if _normalize_section_key(line.text) in {'contents', 'table of contents'}]
        for _, line, right in candidates:
            aligned_keys = {key for key, _, edge in candidates if abs(edge - right) <= 8}
            if any(y < line.y0 for y in titles) or len(aligned_keys) >= 3:
                excluded.add(id(line))
    return excluded


def _find_section_regions(lines: list[_PdfLine], section_keys: set[str]) -> dict[str, tuple[tuple[int, float], Optional[tuple[int, float]]]]:
    """Find section/subsection ranges from rendered PDF headings.

    Text extraction sees figure labels and prose before real headings. We first
    collect exact normalized section-heading text and then prefer bold/numbered
    heading lines. This prevents e.g. the Figure 2 label "Scaled Dot-Product
    Attention" from becoming the start of section 3.2.1.
    """
    best_by_key: dict[str, _PdfLine] = {}
    exact_by_key = {key: [line for line in lines if _section_line_matches(key, line.text)]
                    for key in section_keys}
    contents = _contents_heading_ids(lines, [(key, line) for key, matches in exact_by_key.items() for line in matches])

    for key in section_keys:
        exact = [line for line in exact_by_key[key] if id(line) not in contents]
        if not exact:
            continue
        preferred = [line for line in exact if line.is_bold or _has_numbering_peer(line, lines)]
        pool = preferred or exact
        # Prefer the first visually occurring true heading. Synthetic unit-test
        # PDFs usually have no bold font, so exact text remains a fallback.
        best_by_key[key] = sorted(pool, key=lambda line: (line.page, line.y0, line.x0))[0]

    all_headings = sorted(
        [(line.page, line.y0, key) for key, line in best_by_key.items()],
        key=lambda item: (item[0], item[1]),
    )
    regions: dict[str, tuple[tuple[int, float], Optional[tuple[int, float]]]] = {}
    for idx, (page, y0, key) in enumerate(all_headings):
        end: Optional[tuple[int, float]] = None
        if idx + 1 < len(all_headings):
            end = (all_headings[idx + 1][0], all_headings[idx + 1][1])
        regions[key] = ((page, y0), end)
    return regions


def _line_in_region(line: _PdfLine, region: Optional[tuple[tuple[int, float], Optional[tuple[int, float]]]]) -> bool:
    if region is None:
        return True
    start, end = region
    if line.page < start[0] or (line.page == start[0] and line.y0 < start[1] - 2.0):
        return False
    if end is not None and (line.page > end[0] or (line.page == end[0] and line.y0 >= end[1] - 2.0)):
        return False
    return True



def _line_width(line: _PdfLine) -> float:
    return max(0.0, line.x1 - line.x0)


def _is_display_math_atom(line: _PdfLine) -> bool:
    if line.is_group or line.is_bold:
        return False
    width = _line_width(line)
    if width <= 0 or line.page_w <= 0:
        return False
    if width > line.page_w * 0.86 and line.math_fraction < 0.75:
        return False
    if line.math_fraction >= 0.35:
        return True
    # Short equation fragments may contain roman words like softmax or max.
    return width <= line.page_w * 0.60 and _looks_like_pdf_math_line(line.text)


def _is_table_like_math_atom(line: _PdfLine, page_lines: list[_PdfLine]) -> bool:
    same_row = [
        other for other in page_lines
        if other is not line
        and _is_display_math_atom(other)
        and abs(other.y0 - line.y0) <= 3.5
    ]
    if not same_row:
        return False
    row_boxes = [line.bbox] + [other.bbox for other in same_row]
    span = max(box[2] for box in row_boxes) - min(box[0] for box in row_boxes)
    return len(same_row) >= 1 and span > line.page_w * 0.20


def _combine_pdf_lines(group: list[_PdfLine], line_index: int, group_kind: str = "") -> _PdfLine:
    bbox = _union_bbox([line.bbox for line in group])
    norm_chars: list[tuple[str, BBox]] = []
    tokens: set[str] = set()
    weighted_math = 0.0
    weighted_total = 0.0
    for line in group:
        norm_chars.extend(line.norm_chars)
        tokens.update(line.tokens)
        weight = max(1.0, len(line.norm))
        weighted_math += line.math_fraction * weight
        weighted_total += weight
    text = ' '.join(line.text for line in group)
    norm = ''.join(ch for ch, _ in norm_chars)
    if len(norm) >= 2:
        tokens.add(norm)
    combined = _PdfLine(
        page=group[0].page,
        text=text,
        bbox=bbox,
        page_w=group[0].page_w,
        page_h=group[0].page_h,
        page_rotation=group[0].page_rotation,
        norm=norm,
        norm_chars=norm_chars,
        raw_chars=[item for line in group for item in line.raw_chars],
        tokens=tokens,
        is_bold=False,
        math_fraction=weighted_math / max(1.0, weighted_total),
        is_group=True,
        group_kind=group_kind,
        line_index=line_index,
        nearby_text=' '.join(line.nearby_text or line.text for line in group),
    )
    return combined


def _build_inline_row_groups(lines: list[_PdfLine]) -> list[_PdfLine]:
    """Combine same-visual-row fragments split by the PDF text extractor.

    LaTeX math with superscripts/subscripts can be extracted as several short
    PDF lines at slightly different y positions. Combining a small visual row
    lets queries like W^Q_i locate the full rendered formula instead of only
    the trailing dimension fragment.
    """
    groups: list[_PdfLine] = []
    by_page: dict[int, list[_PdfLine]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)

    next_index = len(lines) + 100000
    seen: set[tuple[int, ...]] = set()
    for page_lines in by_page.values():
        candidates = [
            line for line in page_lines
            if not line.is_bold and (line.math_fraction > 0.0 or _looks_like_pdf_math_line(line.text))
        ]
        for base in candidates:
            cluster = [
                other for other in candidates
                if abs(other.y0 - base.y0) <= 9.0
            ]
            if len(cluster) < 2:
                continue
            cluster = sorted(cluster, key=lambda item: (item.x0, item.y0, item.line_index))
            span = max(line.x1 for line in cluster) - min(line.x0 for line in cluster)
            if span <= 0 or span > base.page_w * 0.90:
                continue
            key = tuple(line.line_index for line in cluster)
            if key in seen:
                continue
            seen.add(key)
            group = _combine_pdf_lines(cluster, next_index, "row")
            group.nearby_text = ' '.join(dict.fromkeys([line.nearby_text or line.text for line in cluster]))
            groups.append(group)
            next_index += 1
    return groups




def _build_inline_stack_groups(lines: list[_PdfLine]) -> list[_PdfLine]:
    """Combine small stacked inline math fragments such as 1 / sqrt(d_k).

    PyMuPDF often exposes the numerator of an inline fraction as a separate
    one-character line while the denominator sits in the surrounding prose line.
    Formula matching by the denominator alone then highlights only sqrt(d_k).
    These synthetic groups keep per-character boxes but order the stacked pieces
    top-to-bottom, so normalized keys like 1sqrtdk can be located precisely.
    """
    groups: list[_PdfLine] = []
    by_page: dict[int, list[_PdfLine]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)

    next_index = len(lines) + 200000
    seen: set[tuple[int, ...]] = set()
    short_atom = re.compile(r'^(?:[0-9]+|[A-Za-z]|[+\-\u2212=().]+)$')
    for page_lines in by_page.values():
        math_lines = [
            line for line in page_lines
            if not line.is_bold and (line.math_fraction > 0.0 or _looks_like_pdf_math_line(line.text))
        ]
        for base in math_lines:
            base_center = (base.x0 + base.x1) / 2.0
            stack = [base]
            for other in page_lines:
                if other is base or other.is_bold:
                    continue
                text = other.text.strip()
                if len(text) > 8 or not short_atom.fullmatch(text):
                    continue
                vertical_close = abs(other.y0 - base.y0) <= 11.0 or abs(other.y1 - base.y1) <= 11.0
                if not vertical_close:
                    continue
                other_center = (other.x0 + other.x1) / 2.0
                overlaps_base = other.x1 >= base.x0 - 4.0 and other.x0 <= base.x1 + 4.0
                centered_on_base = base.x0 - 8.0 <= other_center <= base.x1 + 8.0
                # A denominator embedded in a prose line can be much wider than
                # the actual formula. Accept small numerators that overlap the
                # rendered denominator span or sit inside the full line box.
                if not (overlaps_base or centered_on_base or abs(other_center - base_center) <= 45.0):
                    continue
                stack.append(other)
            if len(stack) < 2:
                continue
            stack = sorted(stack, key=lambda item: (item.y0, item.x0, item.line_index))
            key = tuple(line.line_index for line in stack)
            if key in seen:
                continue
            seen.add(key)
            group = _combine_pdf_lines(stack, next_index, "stack")
            group.nearby_text = ' '.join(dict.fromkeys([line.nearby_text or line.text for line in stack]))
            groups.append(group)
            next_index += 1
    return groups


def _build_display_groups(lines: list[_PdfLine]) -> list[_PdfLine]:
    groups: list[_PdfLine] = []
    by_page: dict[int, list[_PdfLine]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)

    next_index = len(lines)
    for page_lines in by_page.values():
        atoms = [
            line for line in sorted(page_lines, key=lambda l: (l.y0, l.x0))
            if _is_display_math_atom(line)
        ]
        i = 0
        while i < len(atoms):
            group = [atoms[i]]
            j = i + 1
            while j < len(atoms):
                prev = group[-1]
                cur = atoms[j]
                if cur.page != prev.page:
                    break
                vertical_gap = cur.y0 - prev.y1
                same_band = abs(cur.y0 - prev.y0) <= 4.0
                close_vertical = vertical_gap <= 18.0
                center_prev = (prev.x0 + prev.x1) / 2.0
                center_cur = (cur.x0 + cur.x1) / 2.0
                centers_close = abs(center_cur - center_prev) <= max(180.0, prev.page_w * 0.32)
                if (same_band or close_vertical) and centers_close:
                    group.append(cur)
                    j += 1
                    continue
                break
            if len(group) >= 2:
                groups.append(_combine_pdf_lines(group, next_index, "display"))
                next_index += 1
            i = max(j, i + 1)
    return groups


def _union_norm_char_boxes(norm_chars: list[tuple[str, BBox]], start: int, end: int) -> Optional[BBox]:
    boxes = [box for _, box in norm_chars[start:end] if box[2] > box[0] and box[3] > box[1]]
    if not boxes:
        return None
    return _union_bbox(boxes)


def _candidate_variants(tex: str) -> list[str]:
    tex_norm = _norm_string(tex, is_latex=True)
    tokens = [t for t in _tokens_for_matching(tex, is_latex=True) if len(t) >= 2]
    variants = {tex_norm}
    rough = re.sub(r'[_^{}\[\](),;:=+\-−*/\\|]+', ' ', _latex_to_match_text(tex))
    base_words: list[str] = []
    for raw in re.findall(r'[A-Za-z0-9]+|[^\sA-Za-z0-9]', rough):
        n = _norm_string(raw)
        if n:
            base_words.append(n)
    for i in range(len(base_words)):
        acc = ''
        for j in range(i, min(len(base_words), i + 16)):
            acc += base_words[j]
            if len(acc) >= 2:
                variants.add(acc)
            if len(acc) > 100:
                break
    variants.update(tokens)
    if len(tex_norm) == 1:
        variants.add(tex_norm)
    # Sorting a set by length alone leaves same-length ties ordered however
    # the set happened to iterate -- which, for a set of strings, depends on
    # Python's per-process hash randomization (PYTHONHASHSEED), not on
    # anything about the input. Downstream callers (_compact_partial_match,
    # _query_specific_bbox, etc.) pick the first variant that satisfies some
    # condition, so a tied ordering that changes across process restarts
    # could pick a different variant -- and therefore a different match
    # score or bbox -- for the exact same query and document. Breaking ties
    # alphabetically makes the result reproducible.
    return sorted((v for v in variants if len(v) >= 2 or v == tex_norm), key=lambda v: (-len(v), v))



def _span_boxes(norm_chars: list[tuple[str, BBox]], start: int, end: int) -> list[BBox]:
    return [box for _, box in norm_chars[start:end] if box[2] > box[0] and box[3] > box[1]]


def _span_has_large_word_gap(norm_chars: list[tuple[str, BBox]], start: int, end: int) -> bool:
    """Detect accidental matches that cross prose word boundaries.

    The normalized representation removes spaces, so a short query like d_k
    (norm: dk) could otherwise match the final d of one word plus the initial k
    of the next word. Character boxes expose this: prose word gaps are much wider
    than adjacent glyph gaps inside a math token.
    """
    boxes = _span_boxes(norm_chars, start, end)
    if len(boxes) < 2:
        return False
    widths = [max(0.1, b[2] - b[0]) for b in boxes]
    median_width = sorted(widths)[len(widths) // 2]
    for prev, cur in zip(boxes, boxes[1:]):
        prev_y = (prev[1] + prev[3]) / 2.0
        cur_y = (cur[1] + cur[3]) / 2.0
        same_band = abs(prev_y - cur_y) <= max(3.5, median_width * 0.8)
        if not same_band:
            # Superscripts/subscripts can change baseline. They are normally
            # horizontally close or overlapping; a diagonal jump with a wide gap
            # is almost certainly not one formula token.
            horizontal_gap = cur[0] - prev[2]
            if horizontal_gap > max(3.2, median_width * 0.55):
                return True
            continue
        gap = cur[0] - prev[2]
        if gap > max(3.2, median_width * 0.75):
            return True
    return False


def _span_bbox_if_safe(norm_chars: list[tuple[str, BBox]], start: int, end: int, *, short_query: bool = False) -> Optional[BBox]:
    if end <= start:
        return None
    if short_query and _span_has_large_word_gap(norm_chars, start, end):
        return None
    return _union_norm_char_boxes(norm_chars, start, end)


def _tight_bbox_for_candidate_in_line(candidate: FormulaCandidate, line: _PdfLine) -> Optional[BBox]:
    """Return a precise bbox for the candidate within a PDF line.

    Inline formulas use character boxes when the normalized formula appears
    inside surrounding prose. Display formulas are deliberately stricter: a
    single word that also appears in the TeX source must not produce a bbox for
    a heading or prose line. Display candidates therefore require a symbol-heavy
    rendered equation line before any bbox is accepted.
    """
    tex_norm = _norm_string(candidate.latex, is_latex=True)
    is_display = _is_display_candidate(candidate)
    display_safe = _line_is_safe_display_bbox(candidate, line) if is_display else False

    for variant in _candidate_variants(candidate.latex):
        pos = line.norm.find(variant)
        if pos >= 0:
            line_coverage = len(variant) / max(len(line.norm), 1)
            tex_coverage = len(variant) / max(len(tex_norm), 1)
            if is_display:
                if display_safe and (line_coverage >= 0.40 or tex_coverage >= 0.40):
                    return line.bbox
                continue

            # Avoid mapping a long TeX expression to one repeated short token
            # such as d_k. That was a main cause of clicks highlighting an
            # unrelated word or tiny math fragment in the same section.
            if len(tex_norm) > 8 and tex_coverage < 0.30 and line_coverage < 0.45:
                continue

            bbox = _span_bbox_if_safe(line.norm_chars, pos, pos + len(variant), short_query=len(variant) <= 4)
            if bbox is not None:
                return bbox

    token_hits: list[tuple[int, int]] = []
    # Deterministic tie-break (see _candidate_variants) -- _tokens_for_matching
    # returns a set, so ties on length alone would otherwise order however
    # that set's hash-randomized iteration happened to come out this process.
    tokens = sorted(_tokens_for_matching(candidate.latex, is_latex=True), key=lambda v: (-len(v), v))
    for token in tokens:
        if len(token) < 2:
            continue
        pos = line.norm.find(token)
        if pos >= 0:
            token_hits.append((pos, pos + len(token)))

    if token_hits:
        start = min(s for s, _ in token_hits)
        end = max(e for _, e in token_hits)
        coverage = (end - start) / max(len(line.norm), 1)
        if is_display:
            if display_safe and (coverage >= 0.25 or len(token_hits) >= 2):
                return line.bbox
            return None

        tex_span_coverage = (end - start) / max(len(tex_norm), 1)
        if len(tex_norm) > 8 and (len(token_hits) < 2 or tex_span_coverage < 0.30):
            return None

        bbox = _span_bbox_if_safe(line.norm_chars, start, end, short_query=(end - start) <= 4)
        if bbox is not None:
            return bbox

    return None


def _is_display_candidate(candidate: FormulaCandidate) -> bool:
    return bool(candidate.env and candidate.env != 'inline')



def _line_is_safe_display_bbox(candidate: FormulaCandidate, line: _PdfLine) -> bool:
    if not _is_display_candidate(candidate):
        return False
    text = line.text.strip()
    if not text or len(text) > 220:
        return False
    if line.is_bold and not line.is_group:
        return False

    # Prefer lines/groups dominated by TeX math fonts. This rejects section
    # titles and figure labels such as "Scaled Dot-Product Attention" while
    # accepting multi-line display-equation groups whose text may include words
    # like Attention, MultiHead, or where.
    if line.math_fraction >= 0.35:
        return True
    if line.is_group and line.math_fraction >= 0.18 and _looks_like_pdf_math_line(text):
        return True

    if not _looks_like_pdf_math_line(text):
        return False

    letters = sum(ch.isalpha() for ch in text)
    math_marks = len(re.findall(
        r'[=+−*/^_√∑∏∫∂≤≥≠≈∞α-ωΑ-Ωˆ˜∝∼∥⊂⊆⊃⊇∃∀∧∨⋅·×÷±∓→←↔]|\bO\s*\(|\b(?:sqrt|softmax|argmax|argmin|log|exp|sin|cos|tan)\b',
        text,
        re.I,
    ))
    structural = len(re.findall(r'[()\[\]{}]', text))
    non_word_symbols = sum((not ch.isalnum() and not ch.isspace()) for ch in text)

    return (
        math_marks >= 1
        or (structural >= 2 and non_word_symbols >= 3 and letters <= 120)
        or (non_word_symbols >= max(3, int(letters * 0.16)))
    )


def _minimum_match_score(tex_norm: str, candidate: FormulaCandidate) -> float:
    if _is_display_candidate(candidate):
        return 0.48
    if len(tex_norm) <= 3:
        return 0.86
    if len(tex_norm) <= 8:
        return 0.68
    return 0.52




def _table_row_hint(candidate: FormulaCandidate) -> Optional[tuple[set[str], int]]:
    """Return (row-label tokens, math-cell index) for a TeX tabular formula."""
    if not _is_float_or_table_candidate(candidate):
        return None
    before = getattr(candidate, 'tex_context_before', '')
    after = getattr(candidate, 'tex_context_after', '')

    row_break_re = re.compile(r'(?:\\hline|\\midrule|\\toprule|\\bottomrule|\\\\)')
    last_break_end = 0
    for m in row_break_re.finditer(before):
        last_break_end = m.end()
    row_prefix = before[last_break_end:]
    suffix_match = row_break_re.search(after)
    row_suffix = after[:suffix_match.start()] if suffix_match else after[:260]
    row_tex = row_prefix + getattr(candidate, 'latex', '') + row_suffix
    cells = row_tex.split('&')
    if len(cells) < 2:
        return None

    formula_col = max(0, row_prefix.count('&') - 1)
    label_cell = cells[0]
    # Remove math expressions from the row label; we want labels like
    # Self-Attention, Recurrent, ConvS2S, base, etc.
    label_cell = re.sub(r'\\rule(?:\[[^\]]*\])?\{[^}]*\}\{[^}]*\}', ' ', label_cell)
    label_cell = re.sub(r'\$.*?\$', ' ', label_cell)
    label_tokens = set(_word_tokens_for_context(label_cell, is_latex=True))
    label_tokens = {tok for tok in label_tokens if len(tok) >= 2}
    if not label_tokens:
        return None
    return label_tokens, formula_col


def _table_line_from_hint(candidate: FormulaCandidate, search_lines: list[_PdfLine]) -> Optional[_PdfLine]:
    hint = _table_row_hint(candidate)
    if hint is None:
        return None
    label_tokens, formula_col = hint
    real_lines = [line for line in search_lines if not line.is_group]
    label_lines: list[_PdfLine] = []
    for line in real_lines:
        if _looks_like_pdf_math_line(line.text):
            continue
        words = set(_word_tokens_for_context(line.text))
        if label_tokens.issubset(words):
            label_lines.append(line)

    for label_line in sorted(label_lines, key=lambda item: (item.page, item.y0, item.x0)):
        row_math = [
            line for line in real_lines
            if line.page == label_line.page
            and line.x0 > label_line.x0 + 20.0
            and abs(line.y0 - label_line.y0) <= 5.5
            and (line.math_fraction > 0.0 or _looks_like_pdf_math_line(line.text))
        ]
        if not row_math:
            continue
        row_math = sorted(row_math, key=lambda item: item.x0)
        if formula_col < len(row_math):
            return row_math[formula_col]
        # Some cells are extracted as one combined line. Use the closest available
        # math cell instead of falling back to a different row.
        return row_math[-1]
    return None


def _build_section_line_cache(
    match_lines: list[_PdfLine],
    regions: dict[str, tuple[tuple[int, float], Optional[tuple[int, float]]]],
    section_keys: set[str],
) -> tuple[dict[str, list[_PdfLine]], dict[str, list[_PdfLine]]]:
    """Precompute per-section eligible-line lists once instead of per candidate.

    _best_line_for_candidate used to rebuild its "eligible" line list with a
    fresh O(len(match_lines)) scan for every single candidate, making
    assign_bboxes_from_pdf effectively O(candidates x lines). Most candidates
    share one of a much smaller number of distinct sections, so scanning once
    per distinct section (heading-excluded, and heading-excluded+region for
    sections with a known region) and reusing the result via O(1) dict lookup
    gives identical results for a fraction of the work.
    """
    heading_excluded: dict[str, list[_PdfLine]] = {}
    region_filtered: dict[str, list[_PdfLine]] = {}
    for key in section_keys:
        if not key:
            continue
        excluded = [line for line in match_lines if not _section_line_matches(key, line.text)]
        heading_excluded[key] = excluded
        region = regions.get(key)
        if region is not None:
            region_filtered[key] = [line for line in excluded if _line_in_region(line, region)]
    return heading_excluded, region_filtered


@lru_cache(maxsize=32768)
def _display_rows(latex: str) -> tuple[str, ...]:
    """Evidence for every substantial row, rather than its best subexpression.

    This is a veto on approximate geometry, never a notation search key.
    Unknown commands retain their names and therefore fail conservatively.
    """
    latex = re.sub(r'\\(?:hspace|vspace)\*?\s*\{[^{}]*\}', '', latex)
    latex = re.sub(r'(\\begin\{array\})\s*\{[^{}]*\}', r'\1', latex)
    rows = re.split(r'\\\\(?:\s*\[[^\]]*\])?', latex)
    return tuple(value for row in rows
                 if len(value := _norm_string(row, is_latex=True)) >= 6)


def _contains_display_rows(rows: tuple[str, ...], line: _PdfLine) -> bool:
    if len(rows) < 2:
        return True
    from difflib import SequenceMatcher
    from backend.math_unicode import math_unicode_latex
    # Normalize default italic glyphs only. This veto must not introduce
    # blackboard/bold equivalences into either retrieval or highlight matching.
    text = ''.join(alias if (alias := math_unicode_latex(ch)) and len(alias) == 1 else ch for ch in line.text)
    normalized = _norm_string(text)
    # PDF fractions can enumerate numerator/denominator glyphs in a different
    # order. For a complete display group, near-total character multiplicity
    # is additional coverage evidence. It never licenses a query subspan:
    # structured highlighting still requires notation and glyph geometry.
    from collections import Counter
    whole = ''.join(rows)
    if len(normalized) > 1.25 * len(whole):
        # A small source equation embedded in a substantially larger derivative
        # or neighboring equation is not the same occurrence.
        return False
    if line.group_kind == 'display' and len(normalized) <= 1.4 * len(whole):
        overlap = sum((Counter(whole) & Counter(normalized)).values())
        if overlap >= .95 * len(whole):
            return True
    cursor = 0
    for row in rows:
        blocks = SequenceMatcher(None, row, normalized[cursor:], autojunk=False).get_matching_blocks()
        if sum(block.size for block in blocks) < 0.80 * len(row):
            return False
        substantial = [block for block in blocks if block.size]
        if not substantial:
            return False
        cursor += substantial[-1].b + substantial[-1].size
    return True


def _display_signs_present(source: str, line: _PdfLine) -> bool:
    from collections import Counter
    wanted = Counter(re.findall(r'(?<!\\)[=+\-]', source))
    present = Counter(re.findall(r'[=+\-]', line.text.replace('\u2212', '-')))
    source_text = _latex_to_match_text(source).lower()
    source_norm = _norm_string(source,is_latex=True)
    words = re.findall(r'[A-Za-z]{3,}',line.text)
    return not (wanted - present) and all(word.lower() in source_text or word.lower() in source_norm for word in words)


def _display_group_bbox(line: _PdfLine, lines: list[_PdfLine]) -> BBox:
    """Include nearby punctuation and fraction digits omitted by math atom grouping."""
    box = line.bbox
    for other in lines:
        text = other.text.strip()
        if (other.page != line.page or other.is_group or not text or len(text) > 6
                or any(ch.isalpha() for ch in text) or re.fullmatch(r'\(\d+\)|\[\d+\]',text)):
            continue
        if (other.y0 >= line.y0-4 and other.y1 <= line.y1+8
                and other.x0 <= box[2]+8 and other.x1 >= box[0]-8):
            box = _union_bbox([box,other.bbox])
    return box


def _best_line_for_candidate(
    candidate: FormulaCandidate,
    lines: list[_PdfLine],
    region: Optional[tuple[tuple[int, float], Optional[tuple[int, float]]]],
    ordinal_in_section: int,
    section_line_cache: Optional[tuple[dict[str, list[_PdfLine]], dict[str, list[_PdfLine]]]] = None,
) -> Optional[tuple[_PdfLine, BBox, float]]:
    tex_norm = _norm_string(candidate.latex, is_latex=True)
    tex_tokens = _tokens_for_matching(candidate.latex, is_latex=True)
    if not tex_norm:
        return None
    if not tex_tokens and len(tex_norm) == 1:
        # Formatting wrappers such as \mathbf{z} are still legitimate inline
        # math targets. They are ambiguous, so the ranking below relies heavily
        # on surrounding context before accepting a bbox.
        tex_tokens = {tex_norm}
    elif not tex_tokens:
        return None

    section_key = _normalize_section_key(candidate.section)
    table_candidate = _is_float_or_table_candidate(candidate)
    use_region = region is not None and not table_candidate
    if section_line_cache is not None:
        heading_excluded, region_filtered = section_line_cache
        eligible = (region_filtered.get(section_key) if use_region else heading_excluded.get(section_key))
        if eligible is None:
            eligible = lines
    else:
        eligible = [
            line for line in lines
            if (not use_region or _line_in_region(line, region))
            and not _section_line_matches(section_key, line.text)
        ]
    if not eligible:
        eligible = lines

    search_lines = eligible
    if _is_display_candidate(candidate):
        # Display TeX equations must be matched only against rendered equation
        # lines/groups. Row groups are for inline formulas split into visual
        # fragments and should not compete with whole display equations.
        rows = _display_rows(candidate.latex)
        search_lines = [
            line for line in eligible
            if getattr(line, 'group_kind', '') != "row" and _line_is_safe_display_bbox(candidate, line)
            and _contains_display_rows(rows, line)
            and (len(rows) < 2 or _display_signs_present(candidate.latex, line))
        ]
        if not search_lines:
            return None
    else:
        # Display groups are deliberately broad bboxes for multi-line display
        # equations; inline matches should use real lines or row groups only.
        search_lines = [line for line in eligible if getattr(line, 'group_kind', '') != "display"]

    if table_candidate:
        table_line = _table_line_from_hint(candidate, search_lines)
        if table_line is not None:
            table_score = _similarity_score(tex_norm, tex_tokens, table_line, candidate)
            tight = _tight_bbox_for_candidate_in_line(candidate, table_line)
            if table_score >= _minimum_match_score(tex_norm, candidate):
                if tight is not None:
                    return table_line, tight, table_score
                if _line_is_safe_display_bbox(candidate, table_line):
                    return table_line, table_line.bbox, table_score

    rank_lines = search_lines
    if candidate.env == "inline" and len(tex_norm) <= 4:
        ctx_tokens = set(_candidate_context_tokens(candidate)[0] + _candidate_context_tokens(candidate)[1])
        if ctx_tokens:
            filtered = [
                line for line in search_lines
                if tex_norm in line.norm or bool(tex_tokens & line.tokens) or bool(ctx_tokens & _line_context_token_set(line))
            ]
            if filtered:
                rank_lines = filtered
    elif tex_tokens:
        filtered = [line for line in search_lines if tex_norm in line.norm or bool(tex_tokens & line.tokens)]
        if filtered:
            rank_lines = filtered

    best_line: Optional[_PdfLine] = None
    best_score = 0.0
    best_anchor_bbox: Optional[BBox] = None
    prose_anchor = _inline_prose_anchor(candidate, tex_norm)
    best_rank = (False, -1.0, 0.0, 0.0, 0.0)
    short_inline = candidate.env == "inline" and len(tex_norm) <= 4
    context_first = candidate.env == "inline" and len(tex_norm) <= 8
    # Floats use context as a tie-breaking boost. For short prose formulas,
    # surrounding words dominate the ambiguous formula text instead.
    score_weight = 0.35 if short_inline and not table_candidate else 1.0
    context_weight = 0.35 if table_candidate else (0.65 if short_inline else 0.0)
    for line in rank_lines:
        anchor_bbox = _line_prose_anchor_bbox(prose_anchor, line)
        if best_rank[0] and anchor_bbox is None:
            continue
        ctx = _context_score(candidate, line) if candidate.env == "inline" else 0.0
        exact_key = 1 if (tex_norm == line.norm or tex_norm in line.tokens) else 0
        region_key = 1.0 if (region is not None and _line_in_region(line, region)) else 0.0
        tie = (ctx, exact_key, region_key) if context_first else (exact_key, ctx, region_key)
        context_boost = context_weight * ctx
        ceiling = score_weight + context_boost
        if short_inline and exact_key:
            ceiling += 0.05
        # Similarity is clamped to [0, 1]. Even a perfect score cannot win
        # when this complete rank (including anchor/context/region ties) cannot
        # beat the incumbent. Keep the original strict-greater tie policy.
        if (anchor_bbox is not None, ceiling, *tie) <= best_rank:
            continue
        score = _similarity_score(tex_norm, tex_tokens, line, candidate, ctx=ctx)
        if _is_display_candidate(candidate) and len(rows) >= 2:
            # These lines already passed the per-row coverage guard. Legacy
            # alignment keys retain Unicode math italics, so their fuzzy score
            # alone can suppress a complete, correctly grouped recurrence.
            score = max(score, .80)
        rank_score = score_weight * score + context_boost
        if short_inline and exact_key:
            rank_score += 0.05
        rank = (anchor_bbox is not None, rank_score, *tie)
        if rank > best_rank:
            best_rank = rank
            best_score = score
            best_line = line
            best_anchor_bbox = anchor_bbox

    passes_threshold = False
    if best_line is not None:
        threshold = _minimum_match_score(tex_norm, candidate)
        passes_threshold = best_score >= threshold
        if not passes_threshold and candidate.env == "inline" and len(tex_norm) <= 4:
            # A context-strong short-symbol hit is safer than a high fuzzy score
            # on a repeated symbol in a footnote or earlier section.
            passes_threshold = best_rank[1] >= 0.58 and _context_score(candidate, best_line) >= 0.45

    if best_line is not None and passes_threshold:
        tight = best_anchor_bbox or _tight_bbox_for_candidate_in_line(candidate, best_line)
        if tight is not None:
            if best_line.group_kind == 'display' and _is_display_candidate(candidate) and len(rows) >= 2:
                tight = _union_bbox([tight,_display_group_bbox(best_line,lines)])
            return best_line, tight, best_score
        if _line_is_safe_display_bbox(candidate, best_line):
            bbox = _display_group_bbox(best_line,lines) if best_line.group_kind == 'display' and len(rows) >= 2 else best_line.bbox
            return best_line, bbox, best_score

    # Source/PDF order is not evidence that an equation occurs on a line.
    # Floats, omitted branches and source revisions all break that assumption.
    return None


def _set_candidate_location(candidate: FormulaCandidate, line: _PdfLine, bbox: BBox, score: float) -> None:
    x0 = max(0.0, min(float(bbox[0]), line.page_w))
    y0 = max(0.0, min(float(bbox[1]), line.page_h))
    x1 = max(0.0, min(float(bbox[2]), line.page_w))
    y1 = max(0.0, min(float(bbox[3]), line.page_h))
    if x1 <= x0 or y1 <= y0:
        return
    candidate.page = line.page
    candidate.bbox = (x0, y0, x1, y1)
    candidate.page_w = float(line.page_w)
    candidate.page_h = float(line.page_h)
    candidate.page_rotation = line.page_rotation
    candidate.pdf_line_text = line.text
    candidate.pdf_line_norm = line.norm
    candidate.pdf_line_norm_chars = list(line.norm_chars)
    candidate.pdf_line_raw_chars = list(line.raw_chars)
    candidate.pdf_line_math_fraction = float(line.math_fraction)
    candidate.pdf_line_has_math_signal = _looks_like_pdf_math_line(line.text)
    candidate.pdf_line_group_kind = line.group_kind
    # A heuristic similarity score, not a calibrated probability.
    candidate.bbox_score = max(0.0, min(1.0, float(score)))
    inside = bbox[0] >= line.bbox[0] and bbox[1] >= line.bbox[1] and bbox[2] <= line.bbox[2] and bbox[3] <= line.bbox[3]
    candidate.bbox_quality = 'pdf-line-char' if bbox != line.bbox and inside else 'pdf-line'


def _resolve_candidate_location(
    candidate: FormulaCandidate,
    ordinal: int,
    match_lines: list[_PdfLine],
    section_line_cache: tuple[dict[str, list[_PdfLine]], dict[str, list[_PdfLine]]],
    regions: dict[str, tuple[tuple[int, float], Optional[tuple[int, float]]]],
    page_dims: dict[int, tuple[float, float]],
) -> tuple:
    """Compute (without mutating candidate) where it belongs in the PDF.

    Kept side-effect-free so the identical function can run in a worker
    process for the parallel path in assign_bboxes_from_pdf: only these small
    result tuples cross the process boundary, not the candidate objects.
    """
    key = _normalize_section_key(candidate.section)
    region = regions.get(key)
    match = _best_line_for_candidate(candidate, match_lines, region, ordinal, section_line_cache)
    if match is not None:
        line, bbox, score = match
        return ('bbox', line, bbox, score)
    if region is not None:
        page = region[0][0]
        dims = page_dims.get(page)
        return ('page', page, dims[0] if dims else None, dims[1] if dims else None)
    return ('none',)


def _apply_candidate_location(candidate: FormulaCandidate, result: tuple) -> None:
    kind = result[0]
    if kind == 'bbox':
        _, line, bbox, score = result
        _set_candidate_location(candidate, line, bbox, score)
    elif kind == 'page':
        _, page, page_w, page_h = result
        candidate.page = page
        candidate.page_rotation = 0  # page_dims is already in display space
        candidate.bbox_quality = 'section-estimate'
        candidate.bbox_score = 0.0
        if page_w is not None:
            candidate.page_w = page_w
        if page_h is not None:
            candidate.page_h = page_h
    else:
        candidate.bbox_quality = 'unlocated'
        candidate.bbox_score = 0.0


@dataclass
class LineIndex:
    """The PDF-side context _resolve_candidate_location needs, built once per
    document. Cheap relative to actually mapping candidates (see
    build_line_index), so it's safe to build eagerly even when the per-
    candidate mapping itself is deferred to search time -- see
    resolve_candidate_bbox / backend/main.py's lazy /process + /search path.
    """
    match_lines: list[_PdfLine]
    section_line_cache: tuple[dict[str, list[_PdfLine]], dict[str, list[_PdfLine]]]
    regions: dict[str, tuple[tuple[int, float], Optional[tuple[int, float]]]]
    page_dims: dict[int, tuple[float, float]]


def build_line_index(pdf_bytes: bytes, candidates: list[FormulaCandidate]) -> Optional[LineIndex]:
    """Build the one-time-per-document context needed to map any candidate.

    Extracted out of assign_bboxes_from_pdf so backend/main.py can build it
    once at /process time (fast: PDF text-layer extraction plus section-
    region detection, no per-candidate matching) and reuse it across many
    later resolve_candidate_bbox() calls at /search time.
    """
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        lines = _extract_pdf_lines(doc)
    if not lines:
        return None

    display_groups = _build_display_groups(lines)
    row_groups = _build_inline_row_groups(lines)
    stack_groups = _build_inline_stack_groups(lines)
    match_lines = lines + display_groups + row_groups + stack_groups

    section_keys = {_normalize_section_key(c.section) for c in candidates if c.section.strip()}
    section_keys.discard('')
    regions = _find_section_regions(lines, section_keys) if section_keys else {}
    section_line_cache = _build_section_line_cache(match_lines, regions, section_keys)
    page_dims: dict[int, tuple[float, float]] = {}
    for line in lines:
        _, width, height = displayed_geometry(None, line.page_w, line.page_h, line.page_rotation)
        page_dims.setdefault(line.page, (float(width), float(height)))

    return LineIndex(match_lines, section_line_cache, regions, page_dims)


def compute_section_ordinals(candidates: list[FormulaCandidate]) -> list[int]:
    """Each candidate's position among same-section candidates, in document
    order. Pure bookkeeping (no PDF line matching), so -- unlike the mapping
    itself -- there's no reason to defer this to search time.
    """
    ordinal_by_section: dict[str, int] = {}
    ordinals: list[int] = []
    for candidate in candidates:
        key = _normalize_section_key(candidate.section)
        ordinal = ordinal_by_section.get(key, 0)
        ordinal_by_section[key] = ordinal + 1
        ordinals.append(ordinal)
    return ordinals


def resolve_candidate_bbox(candidate: FormulaCandidate, ordinal: int, line_index: LineIndex) -> bool:
    """Map a single candidate's PDF location on demand; idempotent.

    Used by /search to map only the handful of candidates a query actually
    matched, instead of assign_bboxes_from_pdf's every-candidate sweep.
    Returns True if the candidate ends up with at least a page mapping.
    """
    if candidate.page != -1 or candidate.bbox is not None or candidate.bbox_quality == 'unlocated':
        return candidate.page != -1
    result = _resolve_candidate_location(
        candidate, ordinal, line_index.match_lines, line_index.section_line_cache, line_index.regions, line_index.page_dims
    )
    _apply_candidate_location(candidate, result)
    return candidate.page != -1


def assign_bboxes_from_pdf(
    candidates: list[FormulaCandidate],
    pdf_bytes: bytes,
    *,
    progress: bool = False,
) -> None:
    """Eagerly assign formula-level PDF targets to every TeX candidate.

    Earlier versions mapped every TeX formula to its section heading and used a
    full-page-width heading rectangle. That caused result clicks to jump to
    headers and drew oversized highlights. This version searches the PDF text
    layer for the rendered formula run/line. If it cannot find a safe formula
    target, it exposes at most page-only navigation and keeps bbox=None.

    Prefer build_line_index() + resolve_candidate_bbox() for a request path
    (like /process + /search) that can defer per-candidate mapping until a
    candidate is actually searched; this function is for callers that want
    the whole document mapped up front regardless (audits/benchmarks, tests).

    NOTE on parallelism: this was prototyped and benchmarked (both a
    ProcessPoolExecutor -- even with a persistent, reused-across-requests pool
    -- and a ThreadPoolExecutor) and deliberately dropped. A fresh worker
    process importing fitz/sympy/rapidfuzz/httpx costs close to a second
    before it does any work, which dwarfed this function's now-fast (0.4-1.9s
    on the bundled fixtures, post rapidfuzz/indexing fixes) sequential
    runtime even amortized across a warm, reused pool -- the per-task
    match_lines payload still has to cross the process boundary every call.
    Threads avoid that IPC cost but don't scale here either: most of the
    per-candidate cost is Python-level (list/dict work in
    _best_line_for_candidate), which holds the GIL, so thread counts above 2
    made things slower, not faster. Sequential is both simpler and faster for
    every workload tested; revisit only with a genuinely shared-memory
    design (e.g. multiprocessing.shared_memory for match_lines) if a
    real-world document turns out to need it.
    """
    if not candidates:
        return

    line_index = build_line_index(pdf_bytes, candidates)
    if line_index is None:
        return

    ordinals = compute_section_ordinals(candidates)
    for candidate_index, candidate in enumerate(candidates):
        if progress and candidate_index % 25 == 0:
            print(f"[formula-map] candidate {candidate_index}/{len(candidates)}", file=sys.stderr, flush=True)
        resolve_candidate_bbox(candidate, ordinals[candidate_index], line_index)


async def fetch_and_parse_arxiv(arxiv_id: str) -> List[FormulaCandidate]:
    candidates, _ = await fetch_and_parse_arxiv_lazy(arxiv_id, map_bboxes=True)
    return candidates


async def fetch_and_parse_arxiv_lazy(
    arxiv_id: str, *, map_bboxes: bool = False
) -> tuple[List[FormulaCandidate], Optional[LineIndex]]:
    """Like fetch_and_parse_arxiv, but by default only builds the (fast)
    LineIndex instead of eagerly mapping every candidate's bbox. Callers that
    want the old eager behavior can pass map_bboxes=True (then the returned
    LineIndex is redundant, since every candidate is already resolved).
    """
    document = await fetch_arxiv_document(arxiv_id, map_bboxes=map_bboxes)
    return document.candidates, document.line_index


@dataclass
class ArxivDocument:
    candidates: list[FormulaCandidate]
    line_index: Optional[LineIndex] = None
    pdf_bytes: bytes = b""
    page_count: int = 0
    pdf_warning: str = ""
    pdf_cache_hit: bool = False


def _prepare_arxiv_document(tex, pdf_bytes, cached_candidates, map_bboxes, line_cache_directory=None):
    """Runs in a document worker; no fitz objects cross the process boundary."""
    from backend.processing import validate_pdf
    candidates = parse_tex_formulas(tex) if cached_candidates is None else cached_candidates
    if not pdf_bytes:
        return candidates, None, 0, "PDF unavailable; source formulas remain searchable."
    try:
        page_count = validate_pdf(pdf_bytes)
        if map_bboxes:
            assign_bboxes_from_pdf(candidates, pdf_bytes)
            line_index = None
        elif line_cache_directory is not None:
            from backend import line_index_cache
            index_key = line_index_cache.make_key(pdf_bytes, candidates, page_count)
            line_index = line_index_cache.load(index_key, page_count, directory=line_cache_directory)
            if line_index is None:
                line_index = build_line_index(pdf_bytes, candidates)
                line_index_cache.store(index_key, line_index, directory=line_cache_directory)
        else:
            line_index = build_line_index(pdf_bytes, candidates)
    except Exception:
        # A broken/missing PDF must not discard successfully parsed TeX formulas.
        return candidates, None, 0, "PDF could not be indexed; source formulas remain searchable."
    return candidates, line_index, page_count, ""


async def fetch_arxiv_document(arxiv_id: str, *, cached_candidates=None, map_bboxes=False) -> ArxivDocument:
    """Fetch source and PDF together, retaining exactly the bytes used for mapping."""
    from backend.processing import run_document_job
    from backend import cache
    arxiv_id = await resolve_arxiv_id(arxiv_id)
    cached_pdf = await asyncio.to_thread(load_pdf_artifact, arxiv_id)
    async def pdf_content():
        return cached_pdf if cached_pdf is not None else await _fetch_pdf_bytes(arxiv_id)
    if cached_candidates is None:
        tex_result, pdf_result = await asyncio.gather(
            fetch_tex_source(arxiv_id), pdf_content(), return_exceptions=True,
        )
        if isinstance(tex_result, BaseException):
            raise tex_result
    else:
        tex_result = ""
        try:
            pdf_result = await pdf_content()
        except Exception as exc:
            pdf_result = exc
    if isinstance(pdf_result, BaseException) and not isinstance(pdf_result, Exception):
        raise pdf_result
    pdf_bytes = b"" if isinstance(pdf_result, Exception) else pdf_result
    candidates, line_index, page_count, warning = await run_document_job(
        _prepare_arxiv_document, tex_result, pdf_bytes, cached_candidates, map_bboxes, str(cache.CACHE_DIR),
    )
    if page_count and cached_pdf is None:
        await asyncio.to_thread(store_pdf_artifact, arxiv_id, pdf_bytes)
    return ArxivDocument(candidates, line_index, pdf_bytes if page_count else b"", page_count, warning, cached_pdf is not None)
