"""Recover nested fractions and radicals from glyphs and drawn math rules.

Structure is admitted only with a drawn rule, compact aligned operands, and
no omitted glyph inside its region. Uncertain layouts remain fragments. Each
inserted TeX character retains a glyph or rule rectangle for exact highlighting.
"""
from dataclasses import dataclass, field
from statistics import median

from .pdf_math import Glyph, Row, _render, _compatible
from .text_layer import _union_bbox


class GlyphIndex:
    """Small page-local spatial buckets for omitted-ink checks."""
    def __init__(self, glyphs):
        self.bins, self.large = {}, []
        for g in glyphs:
            if g.text == ' ':
                continue
            x = int(((g.box[0]+g.box[2])/2)//16)
            lo, hi = int(g.box[1]//16), int(g.box[3]//16)
            if hi-lo > 64:
                self.large.append(g)
            else:
                for y in range(lo, hi+1):
                    self.bins.setdefault((x, y), []).append(g)

    def query(self, region):
        x0, y0, x1, y1 = region
        seen = set()
        for x in range(int(x0//16), int(x1//16)+1):
            for y in range(int(y0//16), int(y1//16)+1):
                for glyph in self.bins.get((x, y), ()):
                    if id(glyph) not in seen:
                        seen.add(id(glyph))
                        yield glyph
        yield from self.large


def _gap_glyphs(x0, x1, baseline, size, known, glyph_index):
    region = (x0-.1, baseline-.5*size, x1+.1, baseline+.3*size)
    return [g for g in glyph_index.query(region) if id(g) not in known and g.text != ' '
            and x0-.1 < (g.box[0]+g.box[2])/2 < x1+.1
            and g.box[1] < region[3] and g.box[3] > region[1]]


@dataclass(eq=False)
class Atom:
    latex: str
    mapping: list
    raw: list
    glyphs: list[Glyph]
    box: tuple
    baseline: float
    size: float
    evidenced: bool
    structured: bool = False
    script_markers: set = field(default_factory=set)
    script_anchor: tuple | None = None


def _descendants(glyph):
    yield glyph
    for child in glyph.word:
        yield from _descendants(child)
    for _, accent in glyph.accents:
        yield accent
    for rows in glyph.scripts.values():
        for row in rows:
            for child in row.glyphs:
                yield from _descendants(child)


def _from_glyph(glyph, provisional):
    latex, mapping, raw = _render(Row([glyph], glyph.y, glyph.size))
    glyphs = list(_descendants(glyph))
    return Atom(latex, mapping, raw, glyphs, _union_bbox(g.box for g in glyphs), glyph.y,
                glyph.size, any(id(g) not in provisional for g in glyphs))


def _combine(atoms):
    atoms = sorted(atoms, key=lambda a: a.box[0])
    return Atom(''.join(a.latex for a in atoms), [c for a in atoms for c in a.mapping],
                [c for a in atoms for c in a.raw], [g for a in atoms for g in a.glyphs],
                _union_bbox(a.box for a in atoms), median(a.baseline for a in atoms),
                median(a.size for a in atoms), any(a.evidenced for a in atoms),
                any(a.structured for a in atoms))


def _rule_box(rule):
    x0, y, x1 = rule
    return (x0, y - .2, x1, y + .2)


def _wrap(command, arguments, decoration, baseline, size, extra_glyphs=()):
    pieces, mapping = [command], [(c, decoration) for c in command]
    for arg in arguments:
        pieces.extend(('{', arg.latex, '}'))
        mapping.extend([('{', decoration), *arg.mapping, ('}', decoration)])
    glyphs = [g for a in arguments for g in a.glyphs] + list(extra_glyphs)
    raw = [c for a in arguments for c in a.raw] + [(g.text, g.box) for g in extra_glyphs]
    return Atom(''.join(pieces), mapping, raw, glyphs,
                _union_bbox([decoration, *(a.box for a in arguments)]), baseline, size, True, True)


def _compact(atoms, size):
    ordered = sorted(atoms, key=lambda a: a.box[0])
    return all(right.box[0] - left.box[2] <= .8 * size for left, right in zip(ordered, ordered[1:]))


def _omitted_glyphs(all_glyphs, consumed, region):
    consumed_ids = {id(g) for g in consumed}
    x0, y0, x1, y1 = region
    return any(id(g) not in consumed_ids and g.text != ' '
               and x0 + .2 < (g.box[0]+g.box[2])/2 < x1 - .2
               and y0 + .2 < (g.box[1]+g.box[3])/2 < y1 - .2 for g in all_glyphs.query(region))


def _radical_rules(radicals, rules):
    pairs = []
    for glyph in radicals:
        candidates = [(abs(x0-glyph.box[2]) + abs(y-glyph.y), (x0, y, x1))
                      for x0, y, x1 in rules if abs(x0-glyph.box[2]) < .3 * glyph.size
                      and min(abs(y-glyph.y), abs(y-(glyph.box[1]+.1*glyph.size))) < .3 * glyph.size]
        if candidates:
            pairs.append((glyph, min(candidates, key=lambda c: c[0])[1]))
    return pairs


def compose_drawn_overlines(rows, all_glyphs, radicals, rules):
    """Attach a short drawn bar only to a unique isolated mathematical letter.

    Fractions retain priority whenever a non-punctuation glyph occupies the
    numerator region. Wide bars, multiple bases, stacked marks and radical
    rules remain for structural reconstruction. Run before script attachment
    so a bar over A does not prevent its external power from attaching to A.
    """
    if not rules:
        return []
    reserved = {rule for _, rule in _radical_rules(radicals, rules)}
    index = GlyphIndex(all_glyphs)
    bases = [g for row in rows for g in row.glyphs
             if g.text.isalpha() and not g.word and not g.accents and not g.excluded
             and (g.math or g.italic or g.bold)]
    base_index = GlyphIndex(bases)
    reach = 1.1 * max((g.size for g in bases), default=0)
    proposals = {}
    for rule in set(rules)-reserved:
        x0, y, x1 = rule
        choices = [g for g in base_index.query((x0-.2, y, x1+.2, y+reach))
                   if .6*g.size <= g.y-y <= 1.1*g.size
                   and .2*g.size <= x1-x0 <= 1.25*g.size
                   and x0-.2 < (g.box[0]+g.box[2])/2 < x1+.2
                   and abs(x0-g.box[0]) <= .2*g.size and abs(x1-g.box[2]) <= .2*g.size]
        if len(choices) != 1:
            continue
        base, = choices
        region = (x0-.2, y-1.25*base.size, x1+.2, base.box[3])
        neighbors = list(index.query(region))
        # A real numerator, a competing base or an unconsumed accent is not
        # silently discarded. Punctuation on a preceding equation is not a
        # mathematical operand and cannot manufacture a comma/K fraction.
        if any(g is not base and g.text not in ' ,;'
               and x0-.2 < (g.box[0]+g.box[2])/2 < x1+.2
               and y-1.25*base.size < g.y <= base.y+.1*base.size for g in neighbors):
            continue
        if _omitted_glyphs(index, [base], _union_bbox([base.box, _rule_box(rule)])):
            continue
        proposals.setdefault(base, []).append(rule)
    used = set()
    for base, choices in proposals.items():
        if len(choices) != 1:
            continue
        rule, = choices
        # A second nearby bar may be a stacked accent, even if it falls just
        # outside the attachment window. Do not recover only its inner mark.
        if any(other != rule and abs(other[0]-rule[0]) < .2*base.size
               and abs(other[2]-rule[2]) < .2*base.size
               and 0 < base.y-other[1] < 1.7*base.size for other in rules):
            continue
        mark = Glyph('', _rule_box(rule), rule[0], rule[1], base.size, '', True)
        base.accents.append((r'\overline', mark))
        base.math = True
        used.add(rule)
    return [rule for rule in rules if rule not in used]


def _fraction(rule, atoms, all_glyphs):
    x0, y, x1 = rule
    width = x1-x0
    nearby = [a for a in atoms if a.box[0] >= x0 - .2 * a.size and a.box[2] <= x1 + .2 * a.size
              and abs(a.baseline-y) < 2.5 * a.size]
    above = [a for a in nearby if a.baseline < y - .08*a.size]
    below = [a for a in nearby if a.baseline > y + .3*a.size]
    if not above or not below:
        return None
    upper_y = max(a.baseline for a in above)
    lower_y = min(a.baseline for a in below)
    numerator = [a for a in above if abs(a.baseline-upper_y) < .12*a.size]
    denominator = [a for a in below if abs(a.baseline-lower_y) < .12*a.size]
    size = median(a.size for a in numerator+denominator)
    if not (.15*size < lower_y-upper_y < 3.5*size):
        return None
    if not _compact(numerator, size) or not _compact(denominator, size):
        return None
    num, den = _combine(numerator), _combine(denominator)
    if any(all(g.text in ' ,;' for g in operand.glyphs) for operand in (num, den)):
        return None
    center = (x0+x1)/2
    if any(abs((a.box[0]+a.box[2])/2-center) > .25*size + .06*width for a in (num, den)):
        return None
    if width - max(num.box[2]-num.box[0], den.box[2]-den.box[0]) > .8*size:
        return None  # table/figure rules extending beyond the operands
    consumed = numerator+denominator
    region = _union_bbox([num.box, den.box, _rule_box(rule)])
    if _omitted_glyphs(all_glyphs, num.glyphs+den.glyphs, region):
        return None
    # Inline fractions often use smaller operands than the surrounding math.
    # Infer the surrounding baseline only from an immediately adjacent atom.
    neighbors = [a for a in atoms if a not in consumed and size <= a.size <= 1.6*size
                 and all(g.text not in '∑∏∫∮∐⋂⋃' for g in a.glyphs)
                 and y+.12*a.size < a.baseline < y+.4*a.size
                 and min(abs(a.box[2]-x0), abs(a.box[0]-x1)) < .85*a.size]
    if neighbors:
        neighbor = min(neighbors, key=lambda a: min(abs(a.box[2]-x0), abs(a.box[0]-x1)))
        baseline, size = neighbor.baseline, neighbor.size
    else:
        baseline = y+.25*size
    return consumed, _wrap(r'\frac', [num, den], _rule_box(rule), baseline, size)


def _radical(glyph, rule, atoms, all_glyphs):
    x0, y, x1 = rule
    possible = [a for a in atoms if a.box[0] >= x0-.2*glyph.size and a.box[2] <= x1+.2*glyph.size
                and y+.2*a.size < a.baseline < y+2.5*glyph.size]
    if not possible:
        return None
    nearest = min(a.baseline for a in possible)
    content = [a for a in possible if abs(a.baseline-nearest) < .12*a.size]
    if not _compact(content, glyph.size):
        return None
    arg = _combine(content)
    if abs(arg.box[0]-x0) > .3*glyph.size or x1-arg.box[2] > .9*glyph.size:
        return None
    # Root indices require separate reconstruction. Refuse an ambiguous n-th
    # root rather than silently changing it to a square root.
    if any(a.box[2] <= glyph.box[0]+.35*glyph.size and a.box[2] >= glyph.box[0]-.4*glyph.size
           and a.size < .86*glyph.size and y-.6*glyph.size < a.baseline < arg.baseline-.4*glyph.size
           for a in atoms):
        return None
    region = (glyph.box[0], min(arg.box[1], y-.25*glyph.size), x1, max(arg.box[3], glyph.box[3]))
    if _omitted_glyphs(all_glyphs, arg.glyphs+[glyph], region):
        return None
    # Some TeX radical fonts report an ascender box well above the visible
    # overbar. Using it would highlight the numerator of an enclosing fraction.
    decoration = (glyph.box[0], y-.2, x1, max(arg.box[3], glyph.box[3]))
    return content, _wrap(r'\sqrt', [arg], decoration, arg.baseline, arg.size, [glyph])


def _join(atoms, all_glyphs):
    groups = []
    baseline_bins = {}
    indexed = {id(g) for a in atoms for g in a.glyphs}
    # Match baselines with a small geometry tolerance, then order within a row.
    for atom in sorted(atoms, key=lambda a: (a.box[0], a.baseline)):
        possible = sorted({i for key in range(int((atom.baseline-.15*atom.size)//4),
                                             int((atom.baseline+.15*atom.size)//4)+1)
                           for i in baseline_bins.get(key, ())})
        choices = [i for i in possible for g in [groups[i]]
                   if abs(g[-1].baseline-atom.baseline) < .12*max(g[-1].size, atom.size)
                   and .8 <= g[-1].size/atom.size <= 1.25
                   and -.2*atom.size <= atom.box[0]-g[-1].box[2] <= .85*atom.size
                   and _compatible([leaf for a in g for leaf in a.glyphs if leaf.run >= 0],
                                   [leaf for leaf in atom.glyphs if leaf.run >= 0])]
        for index in sorted(choices, key=lambda i: abs(atom.box[0]-groups[i][-1].box[2])):
            group = groups[index]
            known = {id(g) for a in [*group, atom] for g in a.glyphs}
            gap = _gap_glyphs(group[-1].box[2], atom.box[0], atom.baseline, atom.size, known, all_glyphs)
            if any(g.text not in ',;' or id(g) in indexed or abs(g.y-atom.baseline) > .15*atom.size for g in gap):
                continue
            old_key, new_key = int(group[-1].baseline//4), int(atom.baseline//4)
            if old_key != new_key:
                baseline_bins[old_key].remove(index)
                baseline_bins.setdefault(new_key, set()).add(index)
            group.extend(_from_glyph(g, {id(g)}) for g in sorted(gap, key=lambda g: g.x))
            group.append(atom)
            break
        else:
            baseline_bins.setdefault(int(atom.baseline//4), set()).add(len(groups))
            groups.append([atom])
    return [_combine(group) for group in groups if any(a.evidenced for a in group)]


def _structured_scripts(atoms, rules, all_glyphs):
    consumed = set()
    for parent in sorted((a for a in atoms if a.structured), key=lambda a: a.size):
        anchor = parent.script_anchor or parent.box
        for marker, sign in (('_', 1), ('^', -1)):
            if marker in parent.script_markers:
                continue
            choices = [a for a in atoms if a is not parent and a not in consumed
                       and .4 <= a.size/parent.size <= .86
                       and .09*parent.size <= sign*(a.baseline-parent.baseline) <= .85*parent.size
                       and -.15*parent.size <= a.box[0]-anchor[2] <= .5*parent.size
                       and not _gap_glyphs(anchor[2], a.box[0], parent.baseline, parent.size,
                                           {id(g) for g in parent.glyphs+a.glyphs}, all_glyphs)
                       and not any(x0-.5 <= a.box[0] <= x1+.5 and
                                   min(a.baseline, parent.baseline) < y < max(a.baseline, parent.baseline)
                                   for x0, y, x1 in rules)]
            if not choices:
                continue
            first = min(choices, key=lambda a: abs(a.box[0]-anchor[2]))
            group = [first]
            for following in sorted(atoms, key=lambda a: a.box[0]):
                if (following not in consumed and following not in group and following is not parent
                        and abs(following.baseline-first.baseline) < .1*first.size
                        and .9 <= following.size/first.size <= 1.1
                        and -.1*first.size <= following.box[0]-group[-1].box[2] <= .3*first.size):
                    group.append(following)
            script = _combine(group)
            parent.latex += marker+'{'+script.latex+'}'
            parent.mapping.extend([(marker, parent.box), ('{', parent.box), *script.mapping, ('}', script.box)])
            parent.raw.extend(script.raw)
            parent.glyphs.extend(script.glyphs)
            parent.box = _union_bbox([parent.box, script.box])
            parent.script_anchor = anchor
            parent.script_markers.add(marker)
            consumed.update(group)
    return [a for a in atoms if a not in consumed]


def reconstruct(rows, all_glyphs, radicals, provisional, rules):
    all_glyphs = GlyphIndex(all_glyphs)
    atoms = [_from_glyph(g, provisional) for row in rows if not row.attached for g in row.glyphs]
    pairs = _radical_rules(radicals, rules)
    reserved = {rule for _, rule in pairs}
    fractions = sorted(set(rules)-reserved, key=lambda r: r[2]-r[0])
    used_rules, used_radicals = set(), set()
    # Each pass consumes a rule or stops. A nesting cap bounds pathological PDFs.
    for _ in range(12):
        changed = False
        for rule in fractions:
            if rule in used_rules:
                continue
            result = _fraction(rule, atoms, all_glyphs)
            if result:
                consumed, combined = result
                atoms = [a for a in atoms if a not in consumed] + [combined]
                used_rules.add(rule)
                changed = True
        previous_count = len(atoms)
        atoms = _structured_scripts(atoms, rules, all_glyphs)
        changed = changed or len(atoms) < previous_count
        for glyph, rule in pairs:
            if id(glyph) in used_radicals:
                continue
            result = _radical(glyph, rule, atoms, all_glyphs)
            if result:
                consumed, combined = result
                atoms = [a for a in atoms if a not in consumed] + [combined]
                used_radicals.add(id(glyph))
                changed = True
        if not changed:
            break
    return _join(_structured_scripts(atoms, rules, all_glyphs), all_glyphs)
