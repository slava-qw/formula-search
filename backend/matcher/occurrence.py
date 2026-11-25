"""Document-scoped formula identities, independent of query highlights."""
import hashlib
import json
import math


def source_anchor(candidate, ordinal):
    """A parser-order-independent anchor, scoped to equal original source sites.

    Insertion before a different source site does not change this key. Editing
    the actual site, or inserting an equal occurrence at that site, can change
    it; such ambiguous history must never be silently guessed.
    """
    if candidate.source != 'tex' or not candidate.source_file or candidate.source_line < 1:
        return ''
    value = [candidate.source_file, candidate.source_line, candidate.source_location_kind, candidate.latex, ordinal]
    return 'anc1:' + hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def identify_occurrences(candidates):
    """Stable across cache reloads, lazy TeX mapping and unrelated OCR pages.

    TeX source order disambiguates repeated includes/macros on the same original
    line. PDF/OCR records use the full extracted rectangle, never a search
    subspan. Unlocated legacy candidates use their occurrence within an equal
    source anchor. These are identities for the current extraction schema, not
    promises to survive arbitrary future changes to source parsing.
    """
    seen = {}
    for candidate in candidates:
        if candidate.source == 'tex':
            anchor = ['tex', candidate.source_file, candidate.source_line,
                      candidate.source_location_kind, candidate.latex]
            if candidate.source_order >= 0:
                anchor += ['source-order', candidate.source_order]
            else:
                key = json.dumps(anchor, ensure_ascii=True)
                ordinal = seen.get(key, 0)
                seen[key] = ordinal + 1
                anchor += ['unlocated-order', ordinal]
        else:
            bbox = candidate.bbox
            located = (candidate.page >= 0 and bbox is not None and len(bbox) == 4
                       and all(math.isfinite(value) for value in bbox)
                       and bbox[2] > bbox[0] and bbox[3] > bbox[1])
            anchor = [candidate.source, candidate.page, candidate.page_rotation, candidate.latex]
            if located:
                anchor += ['rectangle', [float(value) for value in bbox]]
            else:
                key = json.dumps(anchor, ensure_ascii=True)
                ordinal = seen.get(key, 0)
                seen[key] = ordinal + 1
                anchor += ['unlocated-order', ordinal]
        encoded = json.dumps(anchor, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        yield candidate, 'occ1:' + hashlib.sha256(encoded).hexdigest()
