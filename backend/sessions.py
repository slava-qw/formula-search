"""Bounded resident document sessions with publication ordering across eviction.

The byte budget is a conservative estimate of retained Python data, not RSS.
In-flight requests own their own references and are bounded separately.
"""
from collections import OrderedDict
from collections.abc import MutableMapping
from dataclasses import dataclass
import itertools
import os
import threading
import time

from backend.processing import ProcessingBusy
from backend.preparations import PreparationPool


class StalePublication(RuntimeError):
    pass


class SessionTooLarge(ValueError):
    pass


def estimate_session_bytes(session):
    total = 4096 + len(session.pdf_bytes)
    for item in session.index_cache:
        total += 2048 + 4 * sum(len(getattr(item, name, '') or '') for name in
                               ('latex', 'label', 'section', 'env', 'pdf_line_text', 'pdf_line_norm', 'source_file', 'occurrence_id'))
        total += 256 * len(item.tokens)
        if session.line_index is None:
            total += 224 * (len(item.pdf_line_raw_chars or ()) + len(item.pdf_line_norm_chars or ())
                            + len(item.pdf_latex_chars or ()))
    for item in session.candidates_cache:
        total += 1024 + 4 * (len(item.latex) + len(item.tex_context_before) + len(item.tex_context_after) + len(item.source_file))
    if session.line_index:
        for line in session.line_index.match_lines:
            total += 1024 + 4 * (len(line.text) + len(line.norm) + len(line.nearby_text))
            total += 224 * (len(line.raw_chars) + len(line.norm_chars)) + 128 * len(line.tokens)
        # Section caches contain references to existing lines, not fresh glyphs.
        for groups in session.line_index.section_line_cache:
            total += sum(128 + 16 * len(lines) for lines in groups.values())
    return total


@dataclass
class _Entry:
    session: object
    weight: int
    accessed: float


class SessionStore(MutableMapping):
    @classmethod
    def from_environment(cls):
        return cls(max_entries=int(os.environ.get('FORMULA_SEARCH_MAX_SESSIONS', '8')),
                   max_bytes=int(os.environ.get('FORMULA_SEARCH_SESSION_MIB', '256')) * 1024 * 1024,
                   ttl=int(os.environ.get('FORMULA_SEARCH_SESSION_TTL_SECONDS', '2700')))

    def __init__(self, *, max_entries=8, max_bytes=256 * 1024 * 1024, ttl=45 * 60,
                 max_pending=8, clock=time.monotonic):
        if min(max_entries, max_bytes, ttl, max_pending) <= 0:
            raise ValueError('Session limits must be positive')
        self.max_entries, self.max_bytes, self.ttl = max_entries, max_bytes, ttl
        self.max_pending, self.clock = max_pending, clock
        self.lock = threading.RLock()
        self._entries = OrderedDict()
        self._pending = {}
        self._published = {}
        self._counter = itertools.count(1)
        self._bytes = 0
        self.preparations = PreparationPool()

    def begin(self, key):
        with self.lock:
            if sum(map(len, self._pending.values())) >= self.max_pending:
                raise ProcessingBusy('Too many papers are being prepared. Please retry shortly.')
            ticket = next(self._counter)
            self._pending.setdefault(key, set()).add(ticket)
            return ticket

    def finish(self, key, ticket):
        with self.lock:
            pending = self._pending.get(key)
            if pending is not None:
                pending.discard(ticket)
                if not pending:
                    self._pending.pop(key, None)
                    self._published.pop(key, None)

    def _drop(self, key):
        entry = self._entries.pop(key)
        self._bytes -= entry.weight
        return entry

    def _expire(self):
        now = self.clock()
        for key in list(self._entries):
            if now - self._entries[key].accessed >= self.ttl:
                self._drop(key)

    def _check_active(self, key, generation):
        if generation not in self._pending.get(key, ()):
            raise StalePublication('Document request is no longer active.')

    def _check_generation(self, key, generation):
        self._check_active(key, generation)
        previous = self._entries.get(key)
        latest = max(previous.session.generation if previous else 0, self._published.get(key, 0))
        if generation < latest:
            raise StalePublication('A newer document was processed. Retry for the current document.')

    def _record_publication(self, key, generation):
        if key in self._pending:
            self._published[key] = generation

    def _touch(self, key):
        self._entries[key].accessed = self.clock()
        self._entries.move_to_end(key)

    def _reuse(self, key, generation):
        session = self._entries[key].session
        # Identical active requests can finish in either order. Reuse must not
        # lower the publication barrier or discard OCR/lazy mapping additions.
        session.generation = max(session.generation, generation)
        self._touch(key)
        self._record_publication(key, session.generation)
        return session

    def publish(self, key, session):
        weight = estimate_session_bytes(session)
        if weight > self.max_bytes:
            raise SessionTooLarge('This paper exceeds the resident session size limit.')
        with self.lock:
            self._expire()
            self._check_active(key, session.generation)
            previous = self._entries.get(key)
            if (previous and session.preparation_token is not None
                    and previous.session.preparation_token is session.preparation_token):
                return self._reuse(key, session.generation)
            self._check_generation(key, session.generation)
            if (previous and previous.session.source_type == 'tex' and session.source_type == 'text'
                    and previous.session.pdf_bytes and previous.session.pdf_bytes == session.pdf_bytes):
                previous.session.generation = session.generation
                self._touch(key)
                self._record_publication(key, session.generation)
                return previous.session
            if previous:
                self._drop(key)
            self._entries[key] = _Entry(session, weight, self.clock())
            self._bytes += weight
            self._record_publication(key, session.generation)
            self._evict()
            return session

    def reuse_pdf(self, key, pdf_bytes, generation):
        with self.lock:
            self._expire()
            self._check_active(key, generation)
            entry = self._entries.get(key)
            if (entry and entry.session.source_type in {'tex', 'text'} and entry.session.page_count > 0
                    and entry.session.pdf_bytes == pdf_bytes):
                return self._reuse(key, generation)
            self._check_generation(key, generation)
        return None

    def reuse_version(self, key, identifier, generation, checked_at):
        with self.lock:
            self._expire()
            self._check_active(key, generation)
            entry = self._entries.get(key)
            if (entry and entry.session.source_type == 'tex' and entry.session.page_count > 0
                    and entry.session.resolved_arxiv_id == identifier):
                entry.session.version_checked_at = checked_at
                return self._reuse(key, generation)
            self._check_generation(key, generation)
        return None

    def _evict(self):
        while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
            self._drop(next(iter(self._entries)))

    def reweigh(self, key, session, weight):
        """Caller holds session lock and store lock; validate before mutating."""
        if weight > self.max_bytes:
            raise SessionTooLarge('OCR would exceed the resident session size limit.')
        with self.lock:
            entry = self._entries.get(key)
            if entry is None or entry.session is not session:
                raise StalePublication('Document changed while OCR was running.')
            self._bytes += weight - entry.weight
            entry.weight = weight
            self._touch(key)
            self._evict()

    def __getitem__(self, key):
        with self.lock:
            self._expire()
            entry = self._entries[key]
            self._touch(key)
            return entry.session

    def __setitem__(self, key, session):
        # Explicit seeding by tests/tools still obeys the real resident limits.
        ticket = self.begin(key)
        try:
            session.generation = ticket
            self.publish(key, session)
        finally:
            self.finish(key, ticket)

    def __delitem__(self, key):
        with self.lock:
            self._drop(key)

    def __iter__(self):
        with self.lock:
            self._expire()
            return iter(list(self._entries))

    def __len__(self):
        with self.lock:
            self._expire()
            return len(self._entries)

    def stats(self):
        with self.lock:
            self._expire()
            return {'sessions': len(self._entries), 'estimated_bytes': self._bytes,
                    'pending': sum(map(len, self._pending.values())), 'publication_barriers': len(self._published),
                    'max_sessions': self.max_entries, 'max_estimated_bytes': self.max_bytes,
                    'preparations': self.preparations.stats()}
