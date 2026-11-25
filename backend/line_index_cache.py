"""Bounded, optional PDF line-index persistence for reopened source documents.

Only this module's versioned directory is pruned. JSON carries strings and
references; coordinate tables use exact little-endian float64 values, never
pickle. Bump SCHEMA when line extraction/grouping/section-region semantics change.
Source/PDF parser versions and the PyMuPDF version also participate in identity.
"""
from array import array
import base64
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

import fitz
from backend import cache

SCHEMA = 1
MAX_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 32
MAX_AGE_SECONDS = 30 * 24 * 3600
MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_LINES = 100_000
MAX_GLYPHS = 1_000_000
MAX_REFERENCES = 4_000_000
_HEX = re.compile(r'[a-f0-9]{64}')
_lock = threading.RLock()


def _root(directory=None):
    return Path(directory if directory is not None else cache.CACHE_DIR) / f'line-index-v{SCHEMA}'


def make_key(pdf_bytes, candidates, page_count):
    from backend.extractor.arxiv_tex import _normalize_section_key
    sections = sorted({_normalize_section_key(c.section) for c in candidates if c.section.strip()} - {''})
    identity = [SCHEMA, cache.CACHE_VERSION, cache.PDF_TEXT_CACHE_VERSION, fitz.VersionBind,
                hashlib.sha256(pdf_bytes).hexdigest(), page_count, sections]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _encode(index, key):
    if len(index.match_lines) > MAX_LINES:
        raise ValueError('Too many cached lines')
    references = {id(line): number for number, line in enumerate(index.match_lines)}
    boxes, glyphs = {}, {}
    total_refs = 0

    def chars(items):
        nonlocal total_refs
        total_refs += len(items)
        if total_refs > MAX_REFERENCES:
            raise ValueError('Too many cached character references')
        result = []
        for char, box in items:
            box = tuple(box)
            box_id = boxes.get(box)
            if box_id is None:
                box_id = len(boxes)
                boxes[box] = box_id
            glyph = (char, box_id)
            glyph_id = glyphs.get(glyph)
            if glyph_id is None:
                glyph_id = len(glyphs)
                glyphs[glyph] = glyph_id
                if len(glyphs) > MAX_GLYPHS:
                    raise ValueError('Too many cached glyphs')
            result.append(glyph_id)
        return result

    lines = [[line.page, line.text, line.bbox, line.page_w, line.page_h, line.norm,
              chars(line.norm_chars), sorted(line.tokens), line.is_bold, line.math_fraction,
              line.is_group, line.group_kind, line.line_index, line.nearby_text,
              chars(line.raw_chars), line.page_rotation] for line in index.match_lines]
    sections = []
    for groups in index.section_line_cache:
        section = {}
        for title, items in groups.items():
            total_refs += len(items)
            if total_refs > MAX_REFERENCES:
                raise ValueError('Too many cached section references')
            section[title] = [references[id(line)] for line in items]
        sections.append(section)
    coordinates = array('d', (value for box in boxes for value in box))
    if sys.byteorder != 'little':
        coordinates.byteswap()
    payload = {'schema': SCHEMA, 'key': key, 'lines': lines, 'sections': sections,
               'regions': index.regions, 'page_dims': index.page_dims, 'glyphs': list(glyphs),
               'boxes_le_f64': base64.b64encode(coordinates.tobytes()).decode('ascii')}
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError('Cached index exceeds decoded size limit')
    compressed = gzip.compress(raw, compresslevel=1, mtime=0)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ValueError('Cached index exceeds compressed size limit')
    return compressed


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _box(value):
    if not isinstance(value, list) or len(value) != 4 or not all(_number(v) for v in value):
        raise ValueError('Invalid cached rectangle')
    return tuple(value)


def _decode(data, key, page_count):
    from backend.extractor.arxiv_tex import LineIndex, _PdfLine
    if (not isinstance(data, dict) or type(data.get('schema')) is not int
            or data.get('schema') != SCHEMA or data.get('key') != key):
        raise ValueError('Cache identity mismatch')
    coordinates = array('d')
    encoded = data['boxes_le_f64']
    if not isinstance(encoded, str) or len(encoded) > ((MAX_GLYPHS * 32 + 2) // 3) * 4:
        raise ValueError('Invalid coordinate table')
    coordinates.frombytes(base64.b64decode(encoded, validate=True))
    if sys.byteorder != 'little':
        coordinates.byteswap()
    if len(coordinates) % 4 or not all(map(math.isfinite, coordinates)):
        raise ValueError('Invalid coordinate values')
    boxes = [tuple(coordinates[i:i+4]) for i in range(0, len(coordinates), 4)]
    encoded_glyphs = data['glyphs']
    if not isinstance(encoded_glyphs, list) or len(encoded_glyphs) > MAX_GLYPHS:
        raise ValueError('Invalid glyph table')
    glyphs = []
    for glyph in encoded_glyphs:
        if (not isinstance(glyph, list) or len(glyph) != 2 or not isinstance(glyph[0], str)
                or type(glyph[1]) is not int or not 0 <= glyph[1] < len(boxes)):
            raise ValueError('Invalid glyph reference')
        glyphs.append((glyph[0], boxes[glyph[1]]))
    total_refs = 0

    def refs(values, table):
        nonlocal total_refs
        if not isinstance(values, list):
            raise ValueError('Invalid reference list')
        total_refs += len(values)
        if total_refs > MAX_REFERENCES:
            raise ValueError('Too many cached references')
        result = []
        for number in values:
            if type(number) is not int or not 0 <= number < len(table):
                raise ValueError('Invalid cached reference')
            result.append(table[number])
        return result

    dimensions = data['page_dims']
    if not isinstance(dimensions, dict):
        raise ValueError('Invalid page dimensions')
    page_dims = {}
    for page, dims in dimensions.items():
        number = int(page)
        if (str(number) != page or not 0 <= number < page_count or not isinstance(dims, list)
                or len(dims) != 2 or not all(_number(v) and v > 0 for v in dims)):
            raise ValueError('Invalid page dimensions')
        page_dims[number] = tuple(dims)
    encoded_lines = data['lines']
    if not isinstance(encoded_lines, list) or not 0 < len(encoded_lines) <= MAX_LINES:
        raise ValueError('Invalid line table')
    lines = []
    for row in encoded_lines:
        if (not isinstance(row, list) or len(row) != 16 or type(row[0]) is not int
                or row[0] not in page_dims or not all(isinstance(row[i], str) for i in (1, 5, 11, 13))
                or not all(_number(row[i]) and row[i] > 0 for i in (3, 4))
                or type(row[8]) is not bool or type(row[10]) is not bool or not _number(row[9])
                or type(row[12]) is not int or type(row[15]) is not int or row[15] not in (0, 90, 180, 270)
                or not isinstance(row[7], list) or not all(isinstance(token, str) for token in row[7])):
            raise ValueError('Invalid cached line')
        values = list(row)
        values[2] = _box(values[2])
        values[6] = refs(values[6], glyphs)
        values[7] = set(values[7])
        values[14] = refs(values[14], glyphs)
        lines.append(_PdfLine(*values))
    encoded_sections = data['sections']
    if not isinstance(encoded_sections, list) or len(encoded_sections) != 2:
        raise ValueError('Invalid section cache')
    sections = []
    for group in encoded_sections:
        if not isinstance(group, dict):
            raise ValueError('Invalid section group')
        sections.append({title: refs(items, lines) for title, items in group.items()})
    encoded_regions = data['regions']
    if not isinstance(encoded_regions, dict):
        raise ValueError('Invalid section regions')

    def boundary(value):
        if (not isinstance(value, list) or len(value) != 2 or type(value[0]) is not int
                or not 0 <= value[0] < page_count or not _number(value[1])):
            raise ValueError('Invalid section boundary')
        return tuple(value)

    regions = {}
    for title, region in encoded_regions.items():
        if not isinstance(region, list) or len(region) != 2:
            raise ValueError('Invalid section region')
        regions[title] = (boundary(region[0]), boundary(region[1]) if region[1] is not None else None)
    return LineIndex(lines, tuple(sections), regions, page_dims)


def load(key, page_count, *, directory=None):
    if not isinstance(key, str) or not _HEX.fullmatch(key):
        return None
    with _lock:
        try:
            path = _root(directory) / f'{key}.json.gz'
            stat = path.stat()
            if stat.st_size > MAX_COMPRESSED_BYTES or time.time() - stat.st_mtime > MAX_AGE_SECONDS:
                return None
            with path.open('rb') as stream:
                compressed = stream.read(MAX_COMPRESSED_BYTES + 1)
            if len(compressed) > MAX_COMPRESSED_BYTES:
                return None
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                raw = stream.read(MAX_DECODED_BYTES + 1)
            if len(raw) > MAX_DECODED_BYTES:
                return None
            result = _decode(json.loads(raw), key, page_count)
            try:
                path.touch()
            except OSError:
                # A readable cache can still help on a read-only filesystem.
                pass
            return result
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, EOFError):
            return None


def store(key, index, *, directory=None):
    if index is None or not isinstance(key, str) or not _HEX.fullmatch(key):
        return
    temporary = None
    with _lock:
        try:
            content = _encode(index, key)
            root = _root(directory)
            root.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=key + '.', suffix='.tmp', dir=root)
            temporary = Path(name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            destination = root / f'{key}.json.gz'
            temporary.replace(destination)
            _prune(root, newest=destination)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _prune(root, *, newest=None):
    entries = []
    for path in root.glob('*.json.gz'):
        if not _HEX.fullmatch(path.name[:-8]):
            continue
        try:
            stat = path.stat()
            if time.time() - stat.st_mtime > MAX_AGE_SECONDS:
                path.unlink(missing_ok=True)
            else:
                entries.append((stat.st_mtime, path, stat.st_size))
        except OSError:
            continue
    total = sum(size for _, _, size in entries)
    # File write timestamps can lag an earlier utime/touch on Windows. A new
    # insertion is most recent regardless of that filesystem clock granularity.
    entries.sort(key=lambda item: (item[1] == newest, item[0]))
    while entries and (len(entries) > MAX_ENTRIES or total > MAX_BYTES):
        _, path, size = entries.pop(0)
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            pass
