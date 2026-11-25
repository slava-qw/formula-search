"""Context for PDF words, operators, and prose footnote markers."""
from dataclasses import replace

FUNCTIONS = set('sin cos tan cot sec csc sinh cosh tanh coth arcsin arccos arctan log ln lg exp min max lim limsup liminf sup inf det dim ker gcd arg argmin argmax mod Pr'.split())
LIMIT_OPERATORS = {'\\'+name for name in ('min', 'max', 'argmin', 'argmax', 'lim', 'limsup', 'liminf', 'sup', 'inf')}


def compound_operators(tokens):
    """TeX commonly draws arg min / lim sup with a small explicit space."""
    out, i = [], 0
    pairs = {('arg', 'min'), ('arg', 'max'), ('lim', 'inf'), ('lim', 'sup')}
    while i < len(tokens):
        left = tokens[i]
        if len(left) != 3 or left[0].text not in ('a', 'l') or ''.join(g.text for g in left) not in ('arg', 'lim'):
            out.append(left)
            i += 1
            continue
        j = i+1
        if j < len(tokens) and all(g.text.isspace() for g in tokens[j]):
            j += 1
        right = tokens[j] if j < len(tokens) else []
        words = (''.join(g.text for g in left), ''.join(g.text for g in right))
        if (words in pairs and all(not (g.math or g.italic or g.bold) for g in left+right)
                and left[-1].font == right[0].font
                and abs(left[-1].size-right[0].size) < .5 and abs(left[-1].y-right[0].y) < .5
                and 0 <= right[0].box[0]-left[-1].box[2] <= .5*left[-1].size):
            out.append(left+right)
            i = j+1
        else:
            out.append(left)
            i += 1
    return out


def operator_context(tokens, index, operators):
    """A nearby relation/operator can identify an otherwise ambiguous word.

    Skip compact scripts on the right, measuring the following operator's gap
    from the script edge. Never scan through another baseline word or prose.
    A prefix operator on the right starts its own expression; it does not make
    a preceding ordinary word mathematical ("the ∂x", "consider ∇f"). Single
    letter coefficients retain the existing adjacency evidence.
    """
    token = tokens[index]
    for step in (-1, 1):
        edge = token[0].box[0] if step < 0 else token[-1].box[2]
        for distance in range(1, 13):
            i = index+step*distance
            if not 0 <= i < len(tokens):
                break
            other = tokens[i]
            if other[0].text.isspace():
                continue
            first = other[0] if step > 0 else other[-1]
            gap = first.box[0]-edge if step > 0 else edge-first.box[2]
            if gap < -.2*token[0].size or gap > .85*token[0].size:
                break
            if (step > 0 and first.size < .86*token[0].size
                    and .09*token[0].size < abs(first.y-token[0].y) < .85*token[0].size):
                edge = other[-1].box[2]
                continue
            prefix_after_word = step > 0 and len(token) > 1 and first.text in '∂∇∑∏∫'
            if not prefix_after_word and first.text in operators and abs(first.y-token[0].y) < .12*token[0].size:
                return True
            break
    return False


def suppress_prose_footnotes(tokens, operators):
    """A superscript affiliation is not evidence that a person's name is math.

    Retain known functions, all-capital identifiers and words in a math font.
    Suppress only a compact raised numeric/star/dagger marker, and preceding
    prose/initials in the same font and baseline. Math elsewhere stays intact.
    """
    for i, token in enumerate(tokens):
        if len(token) < 3:
            continue
        word = ''.join(g.text for g in token)
        if (len(word) < 3 or not word.isalpha() or word.isupper() or word in FUNCTIONS
                or any(g.math or g.excluded for g in token)):
            continue
        base = token[-1]
        marker = []
        end = i+1
        for k in range(i+1, len(tokens)):
            other = tokens[k]
            if (all(g.text in '0123456789,*∗†‡' for g in other)
                    and all(.45 <= g.size/base.size <= .86
                            and .09*base.size <= base.y-g.y <= .85*base.size for g in other)
                    and -.2*base.size <= other[0].box[0]-(marker[-1].box[2] if marker else base.box[2]) <= .5*base.size):
                marker.extend(other)
                end += 1
            else:
                break
        if not marker:
            continue
        if operator_context(tokens, i, operators):
            continue
        following = next((tokens[k][0].text for k in range(end, len(tokens)) if not tokens[k][0].text.isspace()), '')
        if following == '(':
            continue
        for g in marker:
            g.excluded = True
        for k in range(i, -1, -1):
            previous = tokens[k]
            if any(g.math or g.font != base.font or abs(g.y-base.y) >= .5
                   or abs(g.size-base.size) >= .5
                   or not (g.text.isalpha() or g.text.isspace() or g.text in ".-'’") for g in previous):
                break
            for g in previous:
                g.excluded = True


def group_word(token, command, union_box):
    """Keep a word atomic for scripts while retaining every original glyph."""
    first = token[0]
    return replace(first, text=''.join(g.text for g in token),
                   box=union_box(g.box for g in token), word=token, word_command=command,
                   scripts={}, accents=[])
