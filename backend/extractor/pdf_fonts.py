"""Decode known math glyphs using embedded font names and actual glyph IDs.

PDF ToUnicode tables are sometimes wrong (e.g. an asterisk labeled as an
arrow). Never repair that globally by Unicode value: consult the embedded
glyph. Fonts sharing a family name must agree, since text trace omits subset
prefixes. All caches belong to one open document.
"""
from functools import lru_cache
import re

import fitz

_SUBSET = re.compile(r'^[A-Z]{6}\+')
_KNOWN = re.compile(r'(?:CM(?:R|MI|MIB|SY|BSY)\d+|MSBM\d+|LMMath(?:Italic|Symbols)\d+-(?:Regular|Bold)|PazoMath(?:-Italic|BlackboardBold)?|txsyb)', re.I)
_TX_BLACKBOARD = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', '𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ'))
_NAMED = {
    'mu': 'μ', 'Delta': 'Δ', 'theta': 'θ', 'theta1': 'ϑ',
    # Legacy TeX glyph names follow texmital.enc, not modern Unicode phi names.
    'epsilon': 'ε', 'epsilon1': 'ϵ', 'phi': 'ϕ', 'phi1': 'φ',
    'pi1': 'ϖ', 'rho1': 'ϱ', 'sigma1': 'ς',
    'asteriskmath': '∗', 'circlecopyrt': '◯', 'openbullet': '∘',
    'triangleright': '▷', 'triangleleft': '◁', 'mapsto': '↦',
}


@lru_cache(maxsize=512)
def family(name):
    return _SUBSET.sub('', name)


@lru_cache(maxsize=512)
def math_alphabet(name):
    name = family(name)
    if re.fullmatch(r'(?:CM(?:B)?SY\d+|LMMathSymbols\d+-(?:Regular|Bold))', name, re.I):
        return r'\mathcal'
    if re.fullmatch(r'(?:MSBM\d+|PazoMathBlackboardBold)', name, re.I):
        return r'\mathbb'
    return ''


class DocumentFonts:
    def __init__(self, document):
        self.document = document
        self.fonts = {}
        self.names = {}

    @staticmethod
    def wants(glyph):
        # Wrong/missing ToUnicode entries can even masquerade as ASCII digits.
        return glyph.text != ' ' and bool(_KNOWN.fullmatch(family(glyph.font)))

    def _font(self, xref):
        if xref not in self.fonts:
            try:
                data = self.document.extract_font(xref)[3]
                # Bound extra decoding memory even for unusual embedded fonts.
                self.fonts[xref] = fitz.Font(fontbuffer=data) if data and len(data) <= 4*1024*1024 else None
            except (ValueError, RuntimeError, fitz.mupdf.FzErrorBase):
                self.fonts[xref] = None
        return self.fonts[xref]

    def _name(self, xref, gid):
        key = xref, gid
        if key not in self.names:
            font = self._font(xref)
            try:
                self.names[key] = font.this.fz_get_glyph_name2(gid) if font and 0 <= gid < font.glyph_count else ''
            except (AttributeError, ValueError, RuntimeError, fitz.mupdf.FzErrorBase):
                self.names[key] = ''
        return self.names[key]

    def repair(self, page, glyphs, trace):
        wanted = [g for g in glyphs if self.wants(g)]
        if not wanted:
            return []
        families = {family(g.font) for g in wanted}
        by_family = {}
        for xref, _, _, name, *_ in page.get_fonts(full=True):
            name = family(name)
            if name in families:
                by_family.setdefault(name, set()).add(xref)
        # One raw glyph may have several drawing records (fill + stroke).
        # Require agreement rather than allowing the last record to win.
        traced, missing_bars = {}, {}
        raw_keys = {(family(g.font), ord(g.text), round(g.x, 2), round(g.y, 2)) for g in wanted}
        raw_positions = {}
        for glyph in wanted:
            position = family(glyph.font), round(glyph.x, 2), round(glyph.y, 2)
            raw_positions.setdefault(position, []).append(glyph)
        known_codes = {}
        for span in trace:
            name = family(span['font'])
            if name in by_family:
                for code, _, origin, _ in span['chars']:
                    if code != 0xfffd:
                        position = name, round(origin[0], 2), round(origin[1], 2)
                        known_codes.setdefault(position, set()).add(code)
        for span in trace:
            name = family(span['font'])
            if (name not in by_family or tuple(span.get('dir', (1, 0))) != (1.0, 0.0)
                    or span.get('type') == 3 or span.get('opacity', 1) <= 0):
                continue
            for code, gid, origin, box in span['chars']:
                names = {self._name(xref, gid) for xref in by_family[name]}
                glyph_name = next(iter(names)) if len(names) == 1 else ''
                # txsyb is the TX math-symbol B font: its named A-Z glyphs
                # are blackboard capitals, even when ToUnicode says plain R.
                # Other families and non-letter glyph names keep their rules.
                replacement = (_TX_BLACKBOARD.get(glyph_name) if name.lower() == 'txsyb'
                               else _NAMED.get(glyph_name))
                if code == 0xfffd:
                    # Trace reports U+FFFD where RAWDICT may instead expose a
                    # raw CID (e.g. the mapsto bar as ASCII '7'). Match only the
                    # one glyph left after accounting for known characters at
                    # this position; a superimposed arrow must keep its own ID.
                    position = name, round(origin[0], 2), round(origin[1], 2)
                    remaining = [g for g in raw_positions.get(position, ())
                                 if ord(g.text) not in known_codes.get(position, ())]
                    if len(remaining) > 1:
                        continue
                    if remaining:
                        code = ord(remaining[0].text)
                key = name, code, round(origin[0], 2), round(origin[1], 2)
                traced.setdefault(key, set()).add(replacement)
                if replacement == '↦' and key not in raw_keys:
                    missing_bars[key] = (tuple(box), tuple(origin), span['size'], name)
        for glyph in wanted:
            key = family(glyph.font), ord(glyph.text), round(glyph.x, 2), round(glyph.y, 2)
            choices = traced.get(key, set())
            if len(choices) == 1 and None not in choices:
                glyph.text = next(iter(choices))
                glyph.math = True
                if glyph.text == '↦':
                    # This named glyph is just the bar in TeX's compound
                    # mapsto operator. A matching arrow must supply the rest.
                    glyph.excluded = True
        return [value for key, value in missing_bars.items() if traced[key] == {'↦'}]
