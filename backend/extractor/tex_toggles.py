"""Literal etoolbox toggle arguments; no TeX execution or dynamic name guessing."""
import re

TOGGLE_ASSIGNMENTS = frozenset({'toggletrue', 'togglefalse', 'settoggle'})
TOGGLE_COMMANDS = TOGGLE_ASSIGNMENTS | {'newtoggle', 'providetoggle', 'iftoggle', 'nottoggle'}
_LITERAL_NAME = re.compile(r'[A-Za-z0-9@_:./-]+')
_IF_COMMAND = re.compile(r'\\(if[A-Za-z@]*|unless)\b')


def has_unsupported_condition(text):
    # Primitive/other package conditions can change toggle assignments. Until
    # they are interpreted, keep the earlier conservative both-branch behavior
    # for the whole merged document. Scan before constant masking erases them.
    return any(m.group(1) not in {'iftrue', 'iffalse', 'iftoggle'} for m in _IF_COMMAND.finditer(text))


def literal_name(value):
    # TeX's csname ignores whitespace. Commands and parameter tokens need TeX
    # expansion, which is deliberately outside this literal-name subset.
    value = ''.join(value.split())
    return value if _LITERAL_NAME.fullmatch(value) else None


def argument_spans(source, pos, count, *, read_group, command):
    """Return content ranges and the end of TeX's braced or single-token args.

    Ranges let the caller keep original file/line provenance for a selected
    branch, including branches containing merged input files.
    """
    spans = []
    for _ in range(count):
        while pos < len(source) and source[pos].isspace():
            pos += 1
        if pos == len(source) or source[pos] == '}':
            return None
        if source[pos] == '{':
            body, after = read_group(source, pos)
            if body is None:
                return None
            spans.append((pos + 1, after - 1))
        else:
            token = command.match(source, pos) if source[pos] == '\\' else None
            after = token.end() if token else pos + 1
            spans.append((pos, after))
        pos = after
    return spans, pos
