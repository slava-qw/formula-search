"""Sequential, scoped expansion of common scientific TeX macros.

No TeX execution, file access or evaluation. Unknown commands remain verbatim.
This intentionally does not implement catcodes, package loading or arbitrary
TeX conditionals. Unsupported delimited definitions shadow earlier definitions
but remain unexpanded, rather than inheriting an incorrect earlier meaning.
"""
from dataclasses import dataclass
import re
from .tex_provenance import SourceBuilder, SourceText
from .tex_toggles import TOGGLE_ASSIGNMENTS, TOGGLE_COMMANDS, argument_spans, literal_name, has_unsupported_condition

COMMAND = re.compile(r'\\([A-Za-z@]+|.)', re.DOTALL)
TOKEN = re.compile(r'\\(?:[A-Za-z@]+|.)|\${1,2}|[{}]|[^\\{}$]+', re.DOTALL)
PARAMETER = re.compile(r'##|#([1-9])')
DEFINITIONS = {'newcommand', 'renewcommand', 'providecommand', 'DeclareMathOperator',
               'DeclarePairedDelimiter', 'def', 'gdef', 'edef', 'xdef'}
_MISSING = object()


class ExpansionLimitError(ValueError):
    pass


@dataclass(frozen=True)
class Macro:
    nargs: int
    body: str
    optional_default: str | None = None
    paired_delimiter: bool = False


def skip_space(text, pos):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def read_group(text, start, opener='{', closer='}'):
    """Balanced group; escaped braces and delimiters inside braces are literal."""
    if start >= len(text) or text[start] != opener:
        return None, start
    depth, braces, pos = 1, 0, start + 1
    while pos < len(text):
        if text[pos] == '\\':
            command = COMMAND.match(text, pos)
            pos = command.end() if command else pos + 1
            continue
        ch = text[pos]
        if opener == '[':
            if ch == '{': braces += 1
            elif ch == '}': braces -= 1
        if not braces:
            if ch == opener: depth += 1
            elif ch == closer:
                depth -= 1
                if not depth: return text[start + 1:pos], pos + 1
        pos += 1
    return None, start


def required_group(text, pos):
    return read_group(text, skip_space(text, pos))


def optional_group(text, pos):
    return read_group(text, skip_space(text, pos), '[', ']')


def command_name(text, pos):
    pos = skip_space(text, pos)
    body, after = read_group(text, pos)
    if body is not None:
        match = COMMAND.fullmatch(body.strip())
        return (match.group(1), after) if match else (None, pos)
    match = COMMAND.match(text, pos)
    return (match.group(1), match.end()) if match else (None, pos)


def definition(text, pos, kind):
    if pos < len(text) and text[pos] == '*': pos += 1
    name, pos = command_name(text, pos)
    if not name:
        return None
    if kind in {'newcommand', 'renewcommand', 'providecommand'}:
        nargs, pos = optional_group(text, pos)
        if nargs is not None and (not nargs.isdigit() or not 0 <= int(nargs) <= 9):
            return None
        nargs = int(nargs or 0)
        default, pos = optional_group(text, pos) if nargs else (None, pos)
        body, after = required_group(text, pos)
        macro = Macro(nargs, body, default) if body is not None else None
    elif kind == 'DeclareMathOperator':
        body, after = required_group(text, pos)
        macro = Macro(0, r'\operatorname{' + body + '}') if body is not None else None
    elif kind == 'DeclarePairedDelimiter':
        left, pos = required_group(text, pos)
        right, after = required_group(text, pos)
        body = right
        macro = Macro(1, left + '#1' + right, paired_delimiter=True) if left is not None and right is not None else None
    else:
        opening = text.find('{', pos)
        if opening < 0:
            return None
        parameters = text[pos:opening].strip()
        nums = re.findall(r'#([1-9])', parameters)
        expected = ''.join('#' + str(i + 1) for i in range(len(nums)))
        body, after = read_group(text, opening)
        macro = Macro(len(nums), body) if body is not None and parameters == expected else None
    return (name, macro, after) if body is not None else None


def arguments(text, pos, macro):
    args = []
    if macro.paired_delimiter:
        pos = skip_space(text, pos)
        if pos < len(text) and text[pos] == '*': pos += 1
        _, pos = optional_group(text, pos)
    elif macro.optional_default is not None:
        value, pos = optional_group(text, pos)
        args.append(macro.optional_default if value is None else value)
    for _ in range(macro.nargs - len(args)):
        pos = skip_space(text, pos)
        if pos == len(text): return None
        if text[pos] == '{':
            value, after = read_group(text, pos)
            if value is None: return None
            args.append(value)
            pos = after
        elif text[pos] == '\\':
            match = COMMAND.match(text, pos)
            if not match: return None
            args.append(match.group())
            pos = match.end()
        else:
            args.append(text[pos])
            pos += 1
    body = PARAMETER.sub(lambda m: '#' if m.group() == '##' else
                         args[int(m.group(1)) - 1] if int(m.group(1)) <= len(args) else m.group(), macro.body)
    return body, pos


def builtin_macros():
    macros = {name: Macro(1, '#1') for name in ('ensuremath', 'change', 'claudechange', 'mymacro')}
    macros.update({name: Macro(2, '#2') for name in ('mathcolor', 'textcolor', 'mcol')})
    macros.update({'color': Macro(1, ''), 'defn': Macro(1, r'\text{#1}'),
                   'bm': Macro(1, r'\boldsymbol{#1}'), 'xspace': Macro(0, '')})
    for names, body in [
        (('parenthesis', 'del'), '(#1)'), (('squarebracket',), '[#1]'),
        (('curlybracket', 'cbr', 'set'), r'\{#1\}'),
        (('absolutevalue', 'abs'), '|#1|'), (('doublebar', 'norm'), r'\|#1\|'),
    ]:
        macros.update({name: Macro(1, body, paired_delimiter=True) for name in names})
    return macros


def expand_macros(text: str, *, max_depth=48, max_work_chars=8 * 1024 * 1024, max_steps=500_000) -> str:
    """Expand definitions at the point of use, respecting groups and environments.

    Budgets include intermediate expansion work. Malformed recursive source
    fails explicitly instead of consuming unbounded memory or silently truncating
    a formula. Unknown commands are preserved for downstream renderers.
    """
    scopes = [builtin_macros()]
    groups = ['root']
    steps, chars = 0, 0
    has_toggles = '\\iftoggle' in text or '\\nottoggle' in text
    select_toggles = getattr(text, 'toggle_selection_safe', True) and not has_unsupported_condition(text)
    uncertain_namespace = False

    def lookup(name, default=None):
        for scope in reversed(scopes):
            if name in scope: return scope[name]
        return default

    def forget_toggles(*, opaque=False):
        # A dynamic name may target an outer flag. Uncertainty must survive
        # the surrounding group; a later literal assignment can recover it.
        # Opaque branches additionally require the stronger fallback below.
        nonlocal uncertain_namespace, select_toggles
        uncertain_namespace = True
        if opaque:
            # Either branch could also redefine commands used later. Resuming
            # pruning after a literal toggle assignment would still be a guess.
            select_toggles = False
        for scope in scopes:
            for key in scope:
                if isinstance(key, tuple): scope[key] = None

    def push(kind):
        if len(scopes) > 512: raise ExpansionLimitError('TeX grouping limit exceeded')
        scopes.append({}); groups.append(kind)

    def pop(kind):
        if len(scopes) > 1 and groups[-1] == kind:
            scopes.pop(); groups.pop()

    def assign(name, macro, global_assignment=False):
        if global_assignment:
            for scope in scopes: scope.pop(name, None)
            scopes[0][name] = macro
        else:
            scopes[-1][name] = macro

    def expand(source, depth, *, edef=False):
        nonlocal steps, chars
        if depth > max_depth: raise ExpansionLimitError('TeX macro recursion limit exceeded')
        out, pos = [], 0
        traced = SourceBuilder() if isinstance(source, SourceText) else None
        token_start = 0
        copied_start, copied_end = None, None
        ends_control_word = False

        def flush_copied():
            nonlocal copied_start, copied_end
            if copied_start is not None:
                traced.append_source(source, copied_start, copied_end)
                copied_start = copied_end = None

        def emit(value, *, generated=False, macro=False, preserve_source=False):
            nonlocal chars, copied_start, copied_end, ends_control_word
            chars += len(value)
            if chars > max_work_chars: raise ExpansionLimitError('TeX macro expansion size limit exceeded')
            if has_toggles and value:
                ends_control_word = bool(re.search(r'\\[A-Za-z@]+$', value))
            if traced is None:
                out.append(value)
            else:
                if generated:
                    flush_copied()
                    if preserve_source:
                        traced.append_source(value)
                    else:
                        traced.append_generated(value, source, token_start, macro=macro)
                else:
                    # Most tokens are copied unchanged. Preserve one range for
                    # each stretch, not one allocation/binary search per token.
                    if copied_end != token_start:
                        flush_copied()
                        copied_start = token_start
                    copied_end = token_start + len(value)

        while pos < len(source):
            token_start = pos
            steps += 1
            if steps > max_steps: raise ExpansionLimitError('TeX macro expansion work limit exceeded')
            token = TOKEN.match(source, pos)
            if token is None:
                emit(source[pos]); pos += 1; continue
            full = token.group()
            pos = token.end()
            if full == '{': push('brace')
            elif full == '}': pop('brace')
            elif full in {'$', '$$'}:
                if groups[-1] == 'math:' + full: pop('math:' + full)
                elif full == '$$' and groups[-1] == 'math:$':
                    pop('math:$'); push('math:$')
                else: push('math:' + full)
            elif full.startswith('\\'):
                name = full[1:]
                toggle_name, toggle_pos, global_toggle = name, pos, False
                if name == 'global' and lookup(name, _MISSING) is _MISSING:
                    following = COMMAND.match(source, skip_space(source, pos))
                    if following and following.group(1) in TOGGLE_ASSIGNMENTS:
                        toggle_name, toggle_pos, global_toggle = following.group(1), following.end(), True
                if (select_toggles and toggle_name in TOGGLE_COMMANDS and lookup(toggle_name, _MISSING) is _MISSING
                        and (not edef or toggle_name in {'iftoggle', 'nottoggle'})):
                    conditional = toggle_name in {'iftoggle', 'nottoggle'}
                    count = 3 if conditional else 2 if toggle_name == 'settoggle' else 1
                    parsed = argument_spans(source, toggle_pos, count, read_group=read_group, command=COMMAND)
                    if parsed is not None:
                        spans, after = parsed
                        flag = literal_name(source[slice(*spans[0])])
                        key = ('toggle', flag)
                        state = lookup(key, _MISSING) if flag is not None else _MISSING
                        if conditional and isinstance(state, bool):
                            choice = state if toggle_name == 'iftoggle' else not state
                            left, right = spans[1 if choice else 2]
                            branch = source.cut(left, right) if traced is not None else source[left:right]
                            expanded = expand(branch, depth + 1, edef=edef)
                            if expanded[:1].isalpha() and ends_control_word:
                                emit(' ', generated=True)
                            emit(expanded, generated=True, preserve_source=traced is not None)
                            pos = after
                            if ends_control_word and pos < len(source) and source[pos].isalpha():
                                emit(' ', generated=True)
                            continue
                        if conditional:
                            # Preserve both branches without executing either
                            # branch's definitions or assignments speculatively.
                            forget_toggles(opaque=True)
                            emit(source[token_start:after]); pos = after
                            continue
                        if flag is None:
                            forget_toggles()
                        elif toggle_name in {'newtoggle', 'providetoggle'}:
                            if state is _MISSING: assign(key, None if uncertain_namespace else False)
                        elif state is not _MISSING:
                            if toggle_name == 'settoggle':
                                raw_value = source[slice(*spans[1])]
                                value = {'true': True, 'false': False}.get(raw_value)
                            else:
                                value = toggle_name == 'toggletrue'
                            assign(key, value, global_toggle)
                        emit(' ', generated=True); pos = after
                        continue
                if name in DEFINITIONS:
                    parsed = definition(source, pos, name)
                    if parsed is not None:
                        macro_name, macro, pos = parsed
                        if name != 'providecommand' or lookup(macro_name) is None:
                            if macro is not None and name in {'edef', 'xdef'}:
                                macro = Macro(macro.nargs, expand(macro.body, depth + 1, edef=True), macro.optional_default)
                            assign(macro_name, macro, name in {'gdef', 'xdef'})
                        emit(' ', generated=True)
                        continue
                if name in {'begin', 'end'}:
                    env, after = required_group(source, pos)
                    if env is not None:
                        if name == 'begin': push('env:' + env)
                        else: pop('env:' + env)
                        rendered = full + '{' + env + '}'
                        emit(rendered, generated=source[token_start:after] != rendered)
                        pos = after; continue
                if name in {'begingroup', 'bgroup'}:
                    push('explicit')
                elif name in {'endgroup', 'egroup'}:
                    pop('explicit')
                elif name in {'(', '['}:
                    push('math:' + name)
                elif name in {')', ']'}:
                    pop('math:' + ( '(' if name == ')' else '['))
                macro = lookup(name)
                if macro is not None:
                    call = arguments(source, pos, macro)
                    if call is not None:
                        body, pos = call
                        if name == 'ensuremath' and not any(
                            kind.startswith('math:') or kind.removeprefix('env:').rstrip('*') in
                            {'equation', 'align', 'gather', 'multline', 'eqnarray', 'math', 'displaymath'}
                            for kind in groups
                        ):
                            body = r'\(' + body + r'\)'
                        expanded = expand(body, depth + 1, edef=edef)
                        emit(expanded, generated=True, macro=True)
                        if re.search(r'\\[A-Za-z]+$', expanded) and pos < len(source) and source[pos].isalpha(): emit(' ', generated=True, macro=True)
                        continue
            emit(full)
        if traced is not None:
            flush_copied()
            return traced.finish()
        return ''.join(out)

    return expand(text, 0)
