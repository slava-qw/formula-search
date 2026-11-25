"""Missing Unicode glyph highlights require whole-row evidence first.

Notation search keys stay unchanged. A fallback may search only a uniquely
verified source row and must preserve case, math fonts and supported scripts.
"""
from dataclasses import replace
import math
import re
from backend.math_unicode import math_unicode_latex
from backend.matcher.notation import notation


def verified_query_bbox(query, candidate, legacy_bbox, validate):
    if candidate.source != 'tex' or candidate.pdf_latex_chars or not candidate.pdf_line_raw_chars:
        return None
    if not re.search('[\u210e\U0001d400-\U0001d7ff]', candidate.pdf_line_text):
        return None
    choices = []
    for raw in verified_spans(candidate):
        bounded = replace(candidate, pdf_line_raw_chars=raw, pdf_line_text=''.join(ch for ch, box in raw),
                          pdf_line_norm='', pdf_line_norm_chars=[])
        if not re.search(r'[\\{}_\^]', query):
            aliases = [(plain_alias(ch), box) for ch, box in raw]
            found = legacy_bbox(''.join(plain_alias(ch) for ch in query), replace(bounded, pdf_line_raw_chars=aliases))
            if found is not None:
                choices.append(found)
                continue
        tokens = notation(query).tokens
        if len(tokens) != 1 and not (len(tokens) == 5 and tokens[1] in {'_', '^'} and tokens[2] == '{' and tokens[4] == '}'):
            continue
        for index, (char, base) in enumerate(raw):
            if plain_alias(char) == char or one_token(char) != tokens[0]:
                continue
            box = base
            if len(tokens) == 5:
                if index + 1 == len(raw):
                    continue
                small_char, small = raw[index + 1]
                if one_token(small_char) != tokens[3] or not script_position(base, small, tokens[1]):
                    continue
                if index + 2 < len(raw) and script_position(base, raw[index + 2][1], tokens[1], anchor=small):
                    continue
                box = (min(base[0],small[0]), min(base[1],small[1]), max(base[2],small[2]), max(base[3],small[3]))
            if (box := validate(box, candidate)) is not None:
                choices.append(box)
    if not choices:
        return None
    target = candidate.bbox
    return min(choices, key=lambda box: ((box[0]+box[2])-(target[0]+target[2]))**2 +
                                       ((box[1]+box[3])-(target[1]+target[3]))**2)

def plain_alias(char):
    latex = math_unicode_latex(char)
    return latex if latex is not None and len(latex) == 1 else char

def one_token(char):
    tokens = notation(char).tokens
    return tokens[0] if len(tokens) == 1 else None

def script_position(base, small, marker, anchor=None):
    if not all(math.isfinite(v) for v in (*base, *small)):
        return False
    height, small_height = base[3] - base[1], small[3] - small[1]
    if height <= 0 or base[2] <= base[0] or small[2] <= small[0]:
        return False
    if not .45 <= small_height / height <= .88:
        return False
    right = (anchor or base)[2]
    if not -.25 * height <= small[0] - right <= .4 * height:
        return False
    if small[0] + small[2] <= base[0] + base[2]:
        return False
    if marker == '_':
        return .14 * height <= small[1] - base[1] <= .8 * height and abs(small[3] - base[3]) <= .45 * height
    return (-height <= small[1] - base[1] <= .05 * height and small[3] <= base[1] + .65 * height)

_ENVIRONMENT = re.compile(r'\\(?:begin|end)\{(?:aligned|align|gathered|split)\*?\}')

_WORD = re.compile(r'\\(?:mathsf|mathrm|operatorname)\s*\{([A-Za-z]{2,})\}')

_EVIDENCE = {'=', '+', '-', '(', ')', ',', r'\to', r'\in', r'\approx', r'\leq', r'\geq'}

def source_rows(latex):
    source = _ENVIRONMENT.sub('', latex)
    source = re.sub(r'(?<!\\)&', '', source)
    source = _WORD.sub(lambda m: m.group(1), source)
    for row in source.split(r'\\'):
        tokens = notation(row).tokens
        flat, scripts = [], []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in {'_', '^'}:
                if (not flat or i + 3 >= len(tokens) or tokens[i + 1] != '{' or tokens[i + 3] != '}'
                        or tokens[i + 2] in {'{', '}', '_', '^'} or len(tokens[i + 2]) > 1 and not tokens[i + 2].startswith('\\')):
                    break
                scripts.append((len(flat) - 1, len(flat), token))
                flat.append(tokens[i + 2])
                i += 4
            else:
                flat.append(token)
                i += 1
        else:
            if len(flat) >= 3 and any(t in _EVIDENCE for t in flat):
                yield tuple(flat), scripts

def verified_spans(candidate):
    raw = candidate.pdf_line_raw_chars or []
    if any(len(char) != 1 or char in '\\{}_^' for char, box in raw):
        return []
    glyphs = [(ch, box) for ch, box in raw if not ch.isspace()]
    parsed = notation(''.join(ch for ch, box in glyphs))
    spans = []
    for row, scripts in source_rows(candidate.latex):
        starts = [i for i in range(len(parsed.tokens) - len(row) + 1) if parsed.tokens[i:i + len(row)] == row]
        if len(starts) != 1:
            continue
        start = starts[0]
        for base_index, small_index, marker in scripts:
            base_piece, small_piece = parsed.pieces[start + base_index], parsed.pieces[start + small_index]
            if base_piece.end - base_piece.start != 1 or small_piece.end - small_piece.start != 1:
                break
            base, small = glyphs[base_piece.start][1], glyphs[small_piece.start][1]
            if not script_position(base, small, marker):
                break
            if small_piece.end < len(glyphs) and script_position(base, glyphs[small_piece.end][1], marker, anchor=small):
                break
        else:
            left, right = parsed.pieces[start].start, parsed.pieces[start + len(row) - 1].end
            if left < right:
                spans.append(glyphs[left:right])
    return spans
