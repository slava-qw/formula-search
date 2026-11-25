"""Resolve latest-version aliases before fetching a source/PDF pair."""
import asyncio
from collections import OrderedDict
import threading
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from backend.document_identity import arxiv_id, is_versioned, unversioned
from backend.downloads import download_bytes
from backend.processing import ProcessingBusy

VERSION_TTL_SECONDS = 600
_ATOM = '{http://www.w3.org/2005/Atom}'


async def fetch_version_metadata(identifier: str) -> bytes:
    url = 'https://export.arxiv.org/api/query?' + urlencode({'id_list': identifier})
    return await download_bytes(url, max_bytes=1024 * 1024)


def version_from_atom(payload: bytes, requested: str) -> str:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError('arXiv returned unreadable version metadata') from exc
    if root.tag != _ATOM + 'feed':
        raise ValueError('arXiv returned unexpected version metadata')
    matches = []
    for entry in root.findall(_ATOM + 'entry'):
        resolved = arxiv_id(entry.findtext(_ATOM + 'id', ''))
        if resolved and is_versioned(resolved) and unversioned(resolved) == requested:
            matches.append(resolved)
    if len(matches) != 1:
        raise ValueError('Could not resolve a unique arXiv version. Open a versioned arXiv URL or process the PDF directly.')
    return matches[0]


class ArxivVersionResolver:
    def __init__(self, *, ttl=VERSION_TTL_SECONDS, max_entries=256, interval=3.0, clock=time.monotonic):
        self.ttl, self.max_entries, self.interval, self.clock = ttl, max_entries, interval, clock
        self._cache = OrderedDict()
        self._pending = {}
        self._lock = threading.Lock()
        self._next_request = 0.0

    async def resolve(self, identifier: str) -> str:
        if arxiv_id(f'https://arxiv.org/abs/{identifier}') != identifier:
            raise ValueError('Invalid arXiv identifier')
        if is_versioned(identifier):
            return identifier
        loop = asyncio.get_running_loop()
        with self._lock:
            entry = self._cache.get(identifier)
            if entry and self.clock() - entry[1] < self.ttl:
                self._cache.move_to_end(identifier)
                return entry[0]
            key = (loop, identifier)
            task = self._pending.get(key)
            if task is None:
                if len(self._pending) >= 32:
                    raise ProcessingBusy('Version lookup is busy. Please retry shortly.')
                task = loop.create_task(self._fetch(identifier))
                self._pending[key] = task
                def finished(done):
                    with self._lock:
                        self._pending.pop(key, None)
                    # The caller can leave while this shared lookup continues.
                    if not done.cancelled():
                        done.exception()
                task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _fetch(self, identifier: str) -> str:
        with self._lock:
            now = self.clock()
            delay = max(0.0, self._next_request - now)
            self._next_request = max(now, self._next_request) + self.interval
        if delay:
            await asyncio.sleep(delay)
        resolved = version_from_atom(await fetch_version_metadata(identifier), identifier)
        with self._lock:
            self._cache[identifier] = (resolved, self.clock())
            self._cache.move_to_end(identifier)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        return resolved


_resolver = ArxivVersionResolver()


async def resolve_arxiv_id(identifier: str) -> str:
    return await _resolver.resolve(identifier)
