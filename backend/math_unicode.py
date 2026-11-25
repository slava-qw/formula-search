"""Explicit Unicode math alphabets, with their font distinction intact.

Do not apply general NFKC: that would collapse bold/blackboard letters, circled
numbers and other notation. Only supported mathematical <font> decompositions
are translated. Unsupported styles remain their original Unicode characters.
"""
from functools import lru_cache
import unicodedata


@lru_cache(maxsize=1024)
def math_unicode_latex(char: str) -> str | None:
    if len(char) != 1:
        return None
    if char == '\u210e':  # The italic h lives in Letterlike Symbols.
        return 'h'
    if char in '\u2102\u210d\u2115\u2119\u211a\u211d\u2124':
        return r'\mathbb{' + chr(int(unicodedata.decomposition(char).split()[1], 16)) + '}'
    if not 0x1D400 <= ord(char) <= 0x1D7FF:
        return None
    name = unicodedata.name(char, '')
    decomposition = unicodedata.decomposition(char).split()
    if len(decomposition) != 2 or decomposition[0] != '<font>':
        return None
    base = chr(int(decomposition[1], 16))
    # Longest prefix first. Unlisted combinations (e.g. bold sans italic) are
    # not approximated by a font command with a different visual meaning.
    for style, command in [('BOLD ITALIC', r'\boldsymbol'), ('ITALIC', ''),
                           ('BOLD', r'\mathbf'), ('DOUBLE-STRUCK', r'\mathbb')]:
        prefix = 'MATHEMATICAL ' + style + ' '
        if name.startswith(prefix) and name[len(prefix):].split(' ', 1)[0] in {
            'CAPITAL', 'SMALL', 'DIGIT', 'NABLA', 'PARTIAL', 'EPSILON', 'THETA',
            'KAPPA', 'PHI', 'RHO', 'PI',
        }:
            if style == 'DOUBLE-STRUCK' and not 'A' <= base <= 'Z':
                return None  # The bundled TeX mathbb font only covers capitals.
            if not command:
                # TeX defaults to italic Latin/lowercase Greek, but capital
                # Greek and nabla need an explicit italic variant.
                command = r'\mathit' if '\u0391' <= base <= '\u03a9' or base in '\u03f4\u2207' else ''
            return command + '{' + base + '}' if command else base
    return None
