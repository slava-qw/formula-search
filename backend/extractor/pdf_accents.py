"""Pair single-glyph math accents using overlap, size and baseline evidence.

TeX accents are often separate glyphs on the same baseline as their argument;
their font ascender box is not a drawing-tight rectangle. An overlapping letter
in prose is insufficient evidence. Ambiguous arguments and stacked accents are
left untouched for the structural recognizer rather than guessed.
"""
from collections import defaultdict

_ACCENTS = {'\u02c6': r'\hat', '\u02dc': r'\tilde', '\u00af': r'\bar',
            '\u02c9': r'\bar', '\u20d7': r'\vec'}


def compose_accents(glyphs, word_tokens, skip=()):
    skip = set(skip)
    marks = [g for g in glyphs if g.text in _ACCENTS and not g.excluded and id(g) not in skip]
    if not marks:
        return glyphs
    # Removing accent marks solely for word detection keeps an italic name
    # such as Jo~ao from turning its accented letter into a math variable.
    lines = defaultdict(list)
    for g in glyphs:
        if g.text not in _ACCENTS:
            lines[g.line].append(g)
    prose_letters = set()
    for line in lines.values():
        for token in word_tokens(line):
            if len(token) > 1 and all(g.text.isalpha() and not g.math for g in token):
                prose_letters.update(id(g) for g in token)
    bins = defaultdict(list)
    eligible = set()
    for g in glyphs:
        if g.text in _ACCENTS:
            continue  # modifier-letter accents can satisfy str.isalpha()
        # All letters participate in ambiguity checks, including prose. A mark
        # straddling a math letter and a roman letter has no proven argument.
        if g.text.isalpha():
            bins[int(g.x//16), int(g.y//16)].append(g)
        if (g.text.isalpha() and (g.math or g.italic or g.bold or '\u0370' <= g.text <= '\u03ff')
                and id(g) not in prose_letters and id(g) not in skip and not g.excluded):
            eligible.add(id(g))
    proposals = defaultdict(list)
    # A higher supported mark can be an outer accent even when it is too far
    # from the letter to attach directly. Do not recover only the inner mark.
    nearby = defaultdict(set)
    for accent in marks:
        cx = (accent.box[0]+accent.box[2])/2
        choices = []
        # Scale the search region to the glyph; retain page-local spatial bins.
        reach = 1.5*accent.size
        for x in range(int((accent.x-reach)//16), int((accent.x+reach)//16)+1):
            for y in range(int((accent.y-reach)//16), int((accent.y+reach)//16)+1):
                for base in bins.get((x, y), ()):
                    overlap = min(accent.box[2], base.box[2])-max(accent.box[0], base.box[0])
                    if (.85 <= accent.size/base.size <= 1.18
                            and 0 < accent.box[2]-accent.box[0] <= 1.25*base.size
                            and -.08*base.size <= base.y-accent.y <= 1.25*base.size
                            and abs(cx-(base.box[0]+base.box[2])/2) <= .35*base.size
                            and overlap > 0
                            and overlap >= .15*min(accent.box[2]-accent.box[0], base.box[2]-base.box[0])):
                        nearby[base].add(id(accent))
                        if base.y-accent.y <= .5*base.size:
                            choices.append(base)
        if len(choices) == 1 and id(choices[0]) in eligible:
            proposals[choices[0]].append(accent)
    consumed = set()
    for base, accents in proposals.items():
        if len(accents) != 1 or len(nearby[base]) != 1:
            continue
        accent, = accents
        base.accents.append((_ACCENTS[accent.text], accent))
        base.math = True
        consumed.add(id(accent))
    return [g for g in glyphs if id(g) not in consumed]
