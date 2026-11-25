"""Small non-executing TeX lexical helpers for source extraction.

This is not a TeX interpreter. Constant conditionals are decidable; unknown
conditions retain both branches. Verbatim content and escaped delimiters never
become formulas. All helper offsets refer to the provided source string.
"""
import re


def _blank(text):
    return ''.join('\n' if ch == '\n' else ' ' for ch in text)


def mask_verbatim(text: str) -> str:
    text = re.sub(r'\\begin\{(verbatim\*?|lstlisting|minted)\}.*?\\end\{\1\}',
                  lambda m: _blank(m.group()), text, flags=re.DOTALL)
    return re.sub(r'\\verb\*?([^\w\s]).*?\1', lambda m: _blank(m.group()), text)


_CONDITIONAL = re.compile(r'\\(iftrue|iffalse|ifx|ifnum|ifdim|ifodd|ifvmode|ifhmode|ifmmode|ifinner|ifcat|ifvoid|ifhbox|ifvbox|ifeof|ifcase|ifdefined|ifcsname|iffontchar|else|fi)\b')


def mask_inactive_constants(text: str) -> str:
    out, stack, last = [], [], 0
    for match in _CONDITIONAL.finditer(text):
        active = all(value is not False for value in stack)
        out.append(text[last:match.start()] if active else _blank(text[last:match.start()]))
        command = match.group(1)
        handled = True
        if command.startswith('if'):
            stack.append(True if command == 'iftrue' else False if command == 'iffalse' else None)
        elif command == 'else' and stack:
            if stack[-1] is not None: stack[-1] = not stack[-1]
        elif command == 'fi' and stack:
            stack.pop()
        else:
            handled = False
        out.append(_blank(match.group()) if handled else match.group())
        last = match.end()
    out.append(text[last:] if all(value is not False for value in stack) else _blank(text[last:]))
    return ''.join(out)


def _escaped(text: str, pos: int) -> bool:
    count = 0
    while pos > 0 and text[pos - 1] == '\\':
        count += 1
        pos -= 1
    return bool(count % 2)


def delimited_math(text: str, excluded=()):
    """Yield (start, end, body, env) outside previously found math environments.

    Skip complete excluded ranges before pairing delimiters. Regex matching
    across those ranges and discarding overlaps afterwards loses synchronization.
    """
    ranges = []
    for start, end in sorted(excluded):
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
        else:
            ranges.append((start, end))
    free, last = [], 0
    for start, end in ranges:
        free.append((last, start))
        last = end
    free.append((last, len(text)))
    closers = {'$': '$', '$$': '$$', r'\(': r'\)', r'\[': r'\]'}
    tokens = re.compile(r'\\[()\[\]]|\${1,2}')
    for start, end in free:
        opened = None
        for match in tokens.finditer(text, start, end):
            if _escaped(text, match.start()):
                continue
            token = match.group()
            if opened is not None and opened[0] == '$' and token == '$$':
                # In inline mode these are a closing dollar followed by the
                # next opening dollar ($a$$b$), not a display-math delimiter.
                body = text[opened[2]:match.start()]
                if not re.search(r'\n\s*\n', body):
                    yield opened[1], match.start() + 1, body, 'inline'
                opened = ('$', match.start() + 1, match.end())
                continue
            if opened is None:
                if token in closers:
                    opened = (token, match.start(), match.end())
            elif token == closers[opened[0]]:
                body = text[opened[2]:match.start()]
                env = 'inline' if opened[0] in ('$', r'\(') else 'display'
                if env == 'inline' and re.search(r'\n\s*\n', body):
                    # TeX inline math cannot cross a paragraph. Recover at the
                    # next potential opener rather than index pages of prose.
                    opened = (token, match.start(), match.end()) if token in closers else None
                    continue
                yield opened[1], match.end(), body, env
                opened = None
