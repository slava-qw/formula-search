"""Compact, pickleable source positions for merged and expanded TeX.

Positions describe the opening delimiter/environment of a formula. Generated
math points to the macro call, never to an unused definition. Runs retain line
numbers, rather than a Python object per character of a potentially large paper.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
import re


@dataclass(frozen=True)
class SourceRun:
    start: int
    end: int
    file: str
    line: int
    kind: str = 'literal'
    fixed: bool = False


class SourceText(str):
    def __new__(cls, text='', runs=(), *, toggle_selection_safe=None):
        obj = super().__new__(cls, text)
        obj.runs = tuple(runs)
        obj.starts = tuple(run.start for run in obj.runs)
        obj.newlines = tuple(m.start() for m in re.finditer('\n', text))
        obj.toggle_selection_safe = (getattr(text, 'toggle_selection_safe', True)
                                     if toggle_selection_safe is None else toggle_selection_safe)
        return obj

    @classmethod
    def file(cls, text, filename):
        return cls(text, [SourceRun(0, len(text), filename, 1)] if text else [])

    def location(self, pos):
        index = bisect_right(self.starts, pos) - 1
        if index < 0 or pos >= self.runs[index].end:
            return '', 0, ''
        run = self.runs[index]
        extra = 0 if run.fixed else (
            bisect_left(self.newlines, pos) - bisect_left(self.newlines, run.start))
        return run.file, run.line + extra, run.kind

    def cut(self, start, end):
        builder = SourceBuilder()
        builder.append_source(self, start, end)
        return builder.finish()


class SourceBuilder:
    def __init__(self, max_chars=None):
        self.parts, self.runs = [], []
        self.length = 0
        self.max_chars = max_chars
        self._tail_line = 0
        self.toggle_selection_safe = True

    def _append(self, text, runs):
        if not text:
            return
        if self.max_chars is not None and self.length + len(text) > self.max_chars:
            raise ValueError('Merged TeX source exceeds the processing limit')
        self.parts.append(text)
        for run in runs:
            shifted = replace(run, start=run.start + self.length, end=run.end + self.length)
            previous = self.runs[-1] if self.runs else None
            if (previous and previous.end == shifted.start and previous.file == shifted.file
                    and previous.kind == shifted.kind and previous.fixed == shifted.fixed
                    and self._tail_line == shifted.line):
                self.runs[-1] = replace(previous, end=shifted.end)
            else:
                self.runs.append(shifted)
            self._tail_line = shifted.line + (0 if shifted.fixed else text[run.start:run.end].count('\n'))
        self.length += len(text)

    def append_source(self, source, start=0, end=None):
        self.toggle_selection_safe &= source.toggle_selection_safe
        end = len(source) if end is None else end
        if start >= end:
            return
        runs = []
        index = max(0, bisect_right(source.starts, start) - 1)
        for i in range(index, len(source.runs)):
            run = source.runs[i]
            if run.start >= end:
                break
            left, right = max(start, run.start), min(end, run.end)
            if left < right:
                _, line, _ = source.location(left)
                runs.append(replace(run, start=left - start, end=right - start, line=line))
        self._append(str(source[start:end]), runs)

    def append_generated(self, text, source, pos, *, macro=False):
        self.toggle_selection_safe &= source.toggle_selection_safe
        filename, line, kind = source.location(pos)
        self._append(text, [SourceRun(0, len(text), filename, line,
                                     'macro' if macro else kind, True)])

    def finish(self):
        return SourceText(''.join(self.parts), self.runs, toggle_selection_safe=self.toggle_selection_safe)
