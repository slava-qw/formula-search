"""Conservative mathematical fragments from a PDF's positioned glyphs.

Fonts and operators identify math runs inside prose. Baselines and relative
sizes recover adjacent scripts, including scripts split into separate PDF
lines. Overlapping math accents retain their argument and geometry. Horizontal
rules veto script attachments across fraction bars. The
separate structure stage reconstructs verified fractions and radicals; unknown
layouts remain fragments. This is not a general PDF-to-LaTeX recognizer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
import math

import fitz

from .text_layer import BBox, FormulaCandidate, _looks_like_formula, _union_bbox
from .pdf_fonts import DocumentFonts, math_alphabet
from .pdf_accents import compose_accents
from .pdf_words import FUNCTIONS as _FUNCTIONS, LIMIT_OPERATORS, compound_operators, group_word, suppress_prose_footnotes, operator_context
from backend.pdf_geometry import unrotated_page_size
from backend.math_unicode import math_unicode_latex

_MATH_FONTS = ('cmmi', 'cmsy', 'cmmib', 'msam', 'msbm', 'cmbsy',
               'math', 'mtmi', 'mtsy', 'mt sy', 'mt mi')
_OPERATORS = set('=+−×÷±∓≤≥≠≈≡∈∉⊂⊆⊃⊇∪∩∑∏∫∂∇∞→↦⊗⊕⋅·◯∘▷◁∗')
_PROSE_SHORT = set('a an as at be by do he if in is it me my no of on or so to up us we'.split())
_ADDRESSES = re.compile(r'https?://\S+|\b[A-Za-z]:[\\/]\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
_SYMBOLS = {'−': '-', '·': r'\cdot ', '⋅': r'\cdot ', '×': r'\times ',
            '÷': r'\div ', '±': r'\pm ', '∓': r'\mp ', '≤': r'\leq ',
            '≥': r'\geq ', '≠': r'\neq ', '≈': r'\approx ', '≡': r'\equiv ',
            '∈': r'\in ', '∉': r'\notin ', '∑': r'\sum ', '∏': r'\prod ',
            '∫': r'\int ', '∂': r'\partial ', '∇': r'\nabla ', '∞': r'\infty ',
            '→': r'\to ', '↦': r'\mapsto ', '⊗': r'\otimes ', '⊕': r'\oplus ',
            '∪': r'\cup ', '∩': r'\cap ', '⊂': r'\subset ', '⊆': r'\subseteq ',
            '⊃': r'\supset ', '⊇': r'\supseteq ', 'µ': r'\mu ',
            '◯': r'\bigcirc ', '∘': r'\circ ', '∗': r'\ast ',
            '▷': r'\triangleright ', '◁': r'\triangleleft '}
_ESCAPED_GLYPHS = {'_': r'\_', '%': r'\%', '#': r'\#', '&': r'\&', '$': r'\$',
                   '{': r'\{', '}': r'\}', '\\': r'\backslash '}
_NEGATED_RELATIONS = {'=': '≠', '∈': '∉'}
_OVERLAYS = {'\u0338': _NEGATED_RELATIONS, '↦': {'→': '↦'}}


@dataclass(eq=False, slots=True)
class Glyph:
    text: str
    box: BBox
    x: float
    y: float
    size: float
    font: str
    math: bool
    italic: bool = False
    line: int = -1
    run: int = -1
    excluded: bool = False
    bold: bool = False
    scripts: dict[str, list['Row']] = field(default_factory=dict)
    accents: list[tuple[str, 'Glyph']] = field(default_factory=list)
    word: list['Glyph'] = field(default_factory=list)
    word_command: str = ''
    limits: bool = False


@dataclass(eq=False, slots=True)
class Row:
    glyphs: list[Glyph]
    baseline: float
    size: float
    attached: bool = False

    @property
    def x0(self):
        return self.glyphs[0].box[0]

    @property
    def x1(self):
        return self.glyphs[-1].box[2]


def _math_font(font):
    return any(name in font.lower() for name in _MATH_FONTS)


def _seed(glyph):
    return glyph.math or glyph.text in _OPERATORS or (
        '\u0370' <= glyph.text <= '\u03ff' and glyph.text.isalpha())


def _compose_relations(glyphs):
    """Recover TeX's overprinted negation and mapsto components.

    CMSY commonly reports the slash as a zero-width combining glyph at the
    relation's left edge. Only a unique, same-size/baseline, overlapping known
    relation is admitted, regardless of painting order. Other combining marks
    remain explicit boundaries.
    """
    removed = set()
    relations = [g for g in glyphs if g.text in _NEGATED_RELATIONS or g.text == '→']
    for slash in (g for g in glyphs if g.text == '\u0338' or g.text == '↦' and g.excluded):
        transforms = _OVERLAYS[slash.text]
        choices = [g for g in relations if g.text in transforms
                   and .85 <= slash.size / g.size <= 1.18
                   and abs(slash.y-g.y) <= .12*g.size
                   and g.box[0]-.12*g.size <= slash.x <= g.box[2]+.05*g.size
                   and slash.box[2] <= g.box[2]+.15*g.size]
        if len(choices) == 1:
            relation = choices[0]
            relation.text = transforms[relation.text]
            relation.box = _union_bbox([relation.box, slash.box])
            relation.math = True
            removed.add(id(slash))
    return [g for g in glyphs if id(g) not in removed] if removed else glyphs


def _restore_overlay_slashes(trace, glyph_lines):
    """RAWDICT can omit a zero-width-only line painted after its relation.

    Text trace retains that drawing operation. Read it only on pages with a
    supported relation, and restore only missing negation strokes, leaving
    ordinary text grouping and font-metric rectangles unchanged.
    """
    glyphs = [g for line in glyph_lines for g in line]
    if not any(g.text in _NEGATED_RELATIONS for g in glyphs):
        return
    known = {(round(g.x, 2), round(g.y, 2)) for g in glyphs if g.text == '\u0338'}
    extra = []
    for span in trace:
        if tuple(span.get('dir', (1, 0))) != (1.0, 0.0) or span.get('type') == 3 or span.get('opacity', 1) <= 0:
            continue
        size = span.get('size', 0)
        if not .5 <= size <= 200:
            continue
        for code, _, origin, box in span['chars']:
            if code != 0x338 or not all(math.isfinite(v) for v in (*origin, *box)):
                continue
            key = tuple(round(v, 2) for v in origin)
            if key not in known:
                known.add(key)
                extra.append(Glyph('\u0338', tuple(box), *origin, size, span['font'], True,
                                   line=-len(extra)-1))
    glyph_lines.extend([g] for g in extra)


def _word_tokens(glyphs):
    """Words in one font/size/baseline, retaining punctuation and whitespace."""
    tokens = []
    for g in glyphs:
        if (tokens and g.text.isalpha() and tokens[-1][-1].text.isalpha()
                and g.font == tokens[-1][-1].font
                and abs(g.y - tokens[-1][-1].y) < 0.5
                and abs(g.size - tokens[-1][-1].size) < 0.5
                and g.box[0] - tokens[-1][-1].box[2] < 0.18 * g.size):
            tokens[-1].append(g)
        else:
            tokens.append([g])
    return tokens


def _math_runs(glyphs):
    tokens = compound_operators(_word_tokens(glyphs))
    suppress_prose_footnotes(tokens, _OPERATORS)
    current = []
    for i, token in enumerate(tokens):
        word = ''.join(g.text for g in token)
        if len(token) == 1 and word.isalpha() and (token[0].italic or token[0].bold):
            token[0].math = True
        # Unknown roman function names followed by '(' are useful; this rule
        # only admits the run when it also contains independent math evidence.
        following = next((t[0].text for t in tokens[i+1:i+4] if not t[0].text.isspace()), '')
        small_label = (i > 0 and token[0].size < tokens[i-1][-1].size * .82
                       and tokens[i-1][-1].math
                       and token[0].box[0] - tokens[i-1][-1].box[2] < token[0].size * .3)
        following_script = (i + 1 < len(tokens) and tokens[i+1][0].size < token[-1].size * .86
                            and abs(tokens[i+1][0].y - token[-1].y) > token[-1].size * .09
                            and tokens[i+1][0].box[0] - token[-1].box[2] < token[-1].size * .3)
        script_call = False
        if following_script:
            for after in tokens[i+1:i+12]:
                if after[0].text.isspace():
                    continue
                if after[0].size >= token[-1].size * .86:
                    script_call = after[0].text == '(' and abs(after[0].y-token[-1].y) < .12*token[-1].size
                    break
        math_context = (word.isalpha() and not any(g.math for g in token)
                        and (len(word) > 2 or word.lower() in _PROSE_SHORT)
                        and operator_context(tokens, i, _OPERATORS))
        prose = (word.isalpha() and not any(g.math for g in token)
                 and not small_label and not math_context
                 and not following_script
                 and word not in _FUNCTIONS and following != '(' and not script_call
                 and (len(word) > 2 or word.lower() in _PROSE_SHORT))
        # Undecodable glyphs / accents / radicals are boundaries, never silently
        # deleted between two terms. Their structure needs a future recognizer.
        unsupported = any(unicodedata.category(g.text) in ('Cc', 'Co', 'Cn', 'Mn', 'Mc', 'Me')
                          or g.text in '\ufffd√ˆ˜¯ˉ' or 'cmex' in g.font.lower() or g.excluded for g in token)
        if prose or unsupported:
            if current:
                yield current
                current = []
        else:
            if (len(token) > 1 and word.isalpha()
                    and not any(g.math or g.italic or g.bold for g in token)
                    and (word in _FUNCTIONS or small_label or following_script or following == '(' or script_call or math_context)):
                command = '\\'+word if word in _FUNCTIONS and not small_label else r'\mathrm'
                grouped = group_word(token, command, _union_bbox)
                grouped.math = bool(math_context)
                current.append(grouped)
                token[-1].math = True  # following word labels may be this word's script
            else:
                current.extend(token)
    if current:
        yield current


def _trim(glyphs):
    while glyphs and (glyphs[0].text.isspace() or glyphs[0].text in ',.;:'):
        glyphs = glyphs[1:]
    while glyphs and (glyphs[-1].text.isspace() or glyphs[-1].text in ',.;:'):
        glyphs = glyphs[:-1]
    return glyphs


def _compatible(left, right):
    # Never join through a word, accent, undecodable glyph or other boundary
    # removed from the same PDF line. Different raw lines can hold one formula.
    runs = {g.line: g.run for g in left}
    return all(g.line not in runs or runs[g.line] == g.run for g in right)


def _rows(glyphs):
    # Cluster horizontal rows first, without flattening raised numerators or
    # lower denominators. Binning limits neighbor work even on dense pages.
    buckets = {}
    for g in sorted(glyphs, key=lambda g: (g.x, g.y)):
        if g.text.isspace():
            continue
        key = round(g.y / 2)
        choices = []
        for bin_key in (key-1, key, key+1):
            for row in buckets.get(bin_key, ()):
                if (abs(row.baseline - g.y) <= max(.6, .08 * row.size)
                        and .88 <= g.size / row.size <= 1.14
                        and -.2 * g.size <= g.box[0] - row.x1 <= .85 * g.size
                        and _compatible(row.glyphs, [g])):
                    choices.append(row)
        if choices:
            row = min(choices, key=lambda r: abs(r.baseline - g.y) + abs(g.box[0] - r.x1))
            row.glyphs.append(g)
        else:
            buckets.setdefault(key, []).append(Row([g], g.y, g.size))
    return [r for group in buckets.values() for r in group]


def _horizontal_rules(page):
    rules = []
    # The compact API returns plain coordinates and avoids constructing a
    # Point/Rect object for every segment in figure-heavy research papers.
    for path in page.get_cdrawings():
        if (path.get('width') or 0) > 1.5:
            continue
        for item in path['items']:
            if item[0] == 'l':
                p, q = item[1:3]
                if abs(p[1] - q[1]) < .2 and 2 <= abs(p[0] - q[0]) <= 250:
                    rules.append((min(p[0], q[0]), p[1], max(p[0], q[0])))
            elif item[0] == 're':
                rect = item[1]
                if rect[3] - rect[1] <= 1.5 and 2 <= rect[2] - rect[0] <= 250:
                    rules.append((rect[0], (rect[1] + rect[3]) / 2, rect[2]))
    return rules


def _script_bins(rows):
    bins = {}
    for row in rows:
        for g in row.glyphs:
            anchor = g.word[-1] if g.word else g
            positions = {int(anchor.x//16)}
            if g.word_command in LIMIT_OPERATORS:
                positions.add(int(((g.box[0]+g.box[2])/2)//16))
            for x in positions:
                bins.setdefault((x, int(g.y//16)), []).append((g, row))
    return bins


def _attach_scripts(rows, rules):
    # Small rows may attach to a larger glyph, including within another script.
    # Each parent must be physically adjacent. A fraction rule takes priority
    # over misleading superscript flags in the PDF text extractor.
    bins = _script_bins(rows)
    # Two adjacent indices can share a raw baseline: w_i x_i must not become
    # w_{ii} x. Split a small row where an intervening larger base starts.
    split_rows = []
    for row in rows:
        pieces = [row.glyphs[:1]]
        for previous, glyph in zip(row.glyphs, row.glyphs[1:]):
            bx, by = int(glyph.x // 16), int(glyph.y // 16)
            intervenes = any(
                parent_row is not row and parent.size >= row.size / .86
                and previous.box[2] - .3 <= parent.x
                and parent.box[2] <= glyph.x + .32 * parent.size
                and .09 * parent.size <= abs(parent.y-row.baseline) <= .85 * parent.size
                for x in range(bx-2, bx+1) for y in range(by-2, by+3)
                for parent, parent_row in bins.get((x, y), ()))
            if intervenes:
                pieces.append([])
            pieces[-1].append(glyph)
        split_rows.extend(Row(piece, row.baseline, row.size) for piece in pieces)
    rows[:] = split_rows
    bins = _script_bins(rows)
    for row in sorted(rows, key=lambda r: -r.size):
        first = row.glyphs[0]
        choices = []
        bx, by = int(first.x // 16), int(first.y // 16)
        for x in range(bx-2, bx+3):
            for y in range(by-2, by+3):
                for parent, parent_row in bins.get((x, y), ()):
                    # Differential/gradient operators take adjacent indices and
                    # powers too. Keep the same geometric and fraction vetoes;
                    # general binary operators are not script bases here.
                    if parent_row is row or not parent.text.isalnum() and parent.text not in ')]}∂∇':
                        continue
                    if not _compatible(parent_row.glyphs, row.glyphs):
                        continue
                    ratio = row.size / parent.size
                    delta = row.baseline - parent.y
                    gap = row.x0 - parent.box[2]
                    center_delta = abs((row.x0+row.x1-parent.box[0]-parent.box[2])/2)
                    centered = (parent.word_command in LIMIT_OPERATORS
                                and .35*parent.size <= abs(delta) <= 1.65*parent.size
                                and center_delta <= max(.35*parent.size, .3*(parent.box[2]-parent.box[0]))
                                and parent.box[0]-.5*parent.size <= row.x0
                                and row.x1 <= parent.box[2]+.5*parent.size)
                    adjacent = (.09*parent.size <= abs(delta) <= .85*parent.size
                                and -.32*parent.size <= gap <= .5*parent.size)
                    if not (.45 <= ratio <= .86 and (adjacent or centered)):
                        continue
                    if callable(rules):
                        rules = rules()
                    anchor = parent.word[-1] if parent.word and not centered else parent
                    if any(x0-.5 <= anchor.box[0] and anchor.box[2] <= x1+.5
                           and abs(ry-parent.y) < 1.5*parent.size
                           and row.x1 > x1+.2*parent.size for x0, ry, x1 in rules):
                        continue  # a numerator/denominator script cannot leave its bar
                    if any(x0-.5 <= row.x0 and row.x1 <= x1+.5
                           and abs(ry-row.baseline) < 1.5*row.size
                           and (anchor.box[0] < x0-.2*parent.size or anchor.box[2] > x1+.2*parent.size)
                           for x0, ry, x1 in rules):
                        continue  # f * (dg/dx) must not steal dx as f's subscript
                    if any(x0 - .5 <= first.x <= x1 + .5 and min(parent.y, row.baseline) < ry < max(parent.y, row.baseline)
                           for x0, ry, x1 in rules):
                        continue
                    marker = '^' if delta < 0 else '_'
                    if marker in parent.scripts:
                        continue
                    choices.append(((center_delta if centered else abs(gap)) + abs(delta)*.25, parent, marker, centered))
        if choices:
            # Multiple operators around a centered row give ambiguous scope.
            if len({id(p) for _, p, _, centered in choices if centered}) > 1:
                choices = [item for item in choices if not item[3]]
            if not choices:
                continue
            _, parent, marker, centered = min(choices, key=lambda item: item[0])
            parent.scripts[marker] = [row]
            parent.limits = parent.limits or centered
            row.attached = True


def _render(row):
    text, mapped, raw = [], [], []
    for g in row.glyphs:
        # PDF glyphs are visible characters, not TeX syntax. In particular,
        # set braces must remain visible even in an incomplete fragment.
        if g.word:
            if g.word_command == r'\mathrm':
                rendered = g.word_command+'{'+g.text+'}'
                glyph_mapping = ([(ch, g.box) for ch in g.word_command+'{']
                                 + [(child.text, child.box) for child in g.word] + [('}', g.box)])
            else:
                rendered = g.word_command+' '
                glyph_mapping = [(ch, g.box) for ch in rendered]
            if g.limits:
                rendered += r'\limits'
                glyph_mapping.extend((ch, g.box) for ch in r'\limits')
            raw.extend((child.text, child.box) for child in g.word)
        else:
            encoded = math_unicode_latex(g.text) if len(g.text) == 1 and ord(g.text) > 127 else None
            rendered = encoded if encoded is not None else _SYMBOLS.get(g.text, _ESCAPED_GLYPHS.get(g.text, g.text))
            alphabet = math_alphabet(g.font) if 'A' <= g.text <= 'Z' else ''
            if encoded is not None:
                pass  # Unicode already specifies the alphabet, independent of span flags.
            elif alphabet:
                rendered = alphabet + '{' + rendered + '}'
                if alphabet == r'\mathcal' and g.bold:
                    rendered = r'\boldsymbol{' + rendered + '}'
            elif g.bold and g.text.isalnum():
                rendered = (r'\boldsymbol{' if g.italic else r'\mathbf{') + rendered + '}'
            glyph_mapping = [(ch, g.box) for ch in rendered]
            raw.append((g.text, g.box))
        for command, accent in g.accents:
            rendered = command + '{' + rendered + '}'
            glyph_mapping = [(ch, accent.box) for ch in command+'{'] + glyph_mapping + [('}', accent.box)]
            if accent.text:  # Drawn rules have geometry, but no text glyph.
                raw.append((accent.text, accent.box))
        text.append(rendered)
        mapped.extend(glyph_mapping)
        for marker in ('_', '^'):
            for script in g.scripts.get(marker, ()):
                sub_text, sub_map, sub_raw = _render(script)
                text.append(marker + '{' + sub_text + '}')
                mapped.extend([(marker, g.box), ('{', g.box), *sub_map, ('}', sub_map[-1][1])])
                raw.extend(sub_raw)
    return ''.join(text), mapped, raw


def extract_page(page: fitz.Page, page_num: int, fonts: DocumentFonts):
    raw = page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)
    selected, literal, all_glyphs, radicals = [], [], [], []
    provisional = set()
    glyph_lines = []
    line_id = 0
    for block in raw.get('blocks', ()):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', ()):
            line_id += 1
            # Rotated diagram labels are not horizontal equations.
            if tuple(line.get('dir', (1, 0))) != (1.0, 0.0):
                continue
            glyphs = []
            for span in line.get('spans', ()):
                if not .5 <= span.get('size', 0) <= 200:
                    continue
                font = span.get('font', '')
                math_font = _math_font(font)
                for char in span.get('chars', ()):
                    for text in char['c']:
                        if text and all(math.isfinite(v) for v in (*char['origin'], *char['bbox'])):
                            glyphs.append(Glyph(text, tuple(float(v) for v in char['bbox']),
                                                *char['origin'], span['size'], font, math_font,
                                                bool(span.get('flags', 0) & 2), line_id,
                                                bold=bool(span.get('flags', 0) & 16)))
            glyph_lines.append(glyphs)
    glyphs = [g for line in glyph_lines for g in line]
    needs_fonts = any(fonts.wants(g) for g in glyphs)
    needs_slashes = any(g.text in _NEGATED_RELATIONS for g in glyphs)
    if needs_fonts or needs_slashes:
        trace = page.get_texttrace()
        if needs_fonts:
            for box, origin, size, font in fonts.repair(page, glyphs, trace):
                if .5 <= size <= 200 and all(math.isfinite(v) for v in (*origin, *box)):
                    glyph_lines.append([Glyph('↦', box, *origin, size, font, True,
                                              line=-len(glyph_lines)-1, excluded=True)])
        if needs_slashes:
            _restore_overlay_slashes(trace, glyph_lines)
    all_glyphs = _compose_relations([g for line in glyph_lines for g in line])
    retained = {id(g) for g in all_glyphs}
    literal_lines, literal_ids = set(), set()
    for line_number, line in enumerate(glyph_lines):
        glyphs = [g for g in line if id(g) in retained]
        text = ''.join(g.text for g in glyphs)
        addresses = list(_ADDRESSES.finditer(text))
        for address in addresses:
            for g in glyphs[address.start():address.end()]:
                g.excluded = True
        if not addresses and _looks_like_formula(text):
            literal_lines.add(line_number)
            literal_ids.update(id(g) for g in glyphs)
    all_glyphs = compose_accents(all_glyphs, _word_tokens, literal_ids)
    retained = {id(g) for g in all_glyphs}
    radicals = [g for g in all_glyphs if g.text == '√']
    for line_number, line in enumerate(glyph_lines):
        glyphs = [g for g in line if id(g) in retained]
        if line_number in literal_lines:
            # Preserve literal TeX only when actual TeX syntax exists.
            glyphs = _trim(glyphs)
            literal.append((''.join(g.text for g in glyphs), glyphs))
            continue
        for run_id, run in enumerate(_math_runs(glyphs)):
            run = _trim(run)
            evidenced = any(_seed(g) for g in run)
            numeric = run and all(g.text.isspace() or g.text in '0123456789.,()-' for g in run)
            italic_operand = run and len(run) <= 4 and all(g.text.isalpha() and g.italic for g in run)
            function_operand = len(run) == 1 and bool(run[0].word_command) and run[0].word_command != r'\mathrm'
            if run and (evidenced or numeric or italic_operand or function_operand):
                for g in run:
                    g.run = run_id
                    if not evidenced:
                        provisional.add(id(g))
                        provisional.update(id(child) for child in g.word)
                selected.extend(run)
    rows = _rows(selected)
    rules = None
    def get_rules():
        nonlocal rules
        if rules is None:
            rules = _horizontal_rules(page)
        return rules
    from .pdf_structures import reconstruct, compose_drawn_overlines
    structural_rules = compose_drawn_overlines(rows, all_glyphs, radicals, get_rules()) if rows else []
    _attach_scripts(rows, structural_rules)
    results = []
    for atom in reconstruct(rows, all_glyphs, radicals, provisional, structural_rules):
        latex, mapping, chars = atom.latex, atom.mapping, atom.raw
        if not latex or not any(ch.isalnum() or ch in _OPERATORS for ch, _ in chars):
            continue
        candidate = _candidate(latex, chars, page, page_num, mapping)
        candidate.bbox = atom.box
        results.append(candidate)
    for text, glyphs in literal:
        chars = [(g.text, g.box) for g in glyphs]
        results.append(_candidate(text, chars, page, page_num, chars))
    results.sort(key=lambda c: (c.bbox[1], c.bbox[0]))
    return results


def _candidate(latex, chars, page, page_num, mapping):
    width, height = unrotated_page_size(page)
    return FormulaCandidate(latex=latex, source='text', env='fragment', page=page_num,
                            bbox=_union_bbox(box for _, box in chars), page_w=float(width),
                            page_h=float(height), page_rotation=page.rotation,
                            pdf_line_text=''.join(ch for ch, _ in chars),
                            pdf_line_raw_chars=chars, pdf_line_has_math_signal=True,
                            pdf_latex_chars=mapping,
                            bbox_quality='text-math-fragment', bbox_score=1.0)
