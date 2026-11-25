"""Conservative document identity, mirrored by extension/document-identity.js.

Only official arXiv URLs receive alias normalization. Generic URL query strings,
path case and explicit arXiv versions remain distinct. Shared contract fixtures
live in testdata/document_identity.json.
"""
import re

_URL = re.compile(r'(https?)://([^/?#]+)(/[^?#]*)(?:[?#].*)?', re.IGNORECASE)
_PATH = re.compile(r'/(?:pdf|abs|src|html|e-print)/([A-Za-z-]+(?:\.[A-Za-z]{2})?/[0-9]{7}|[0-9]{4}\.[0-9]{4,5})(v[1-9][0-9]*)?(?:\.pdf)?')
_HOSTS = {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'}


def arxiv_id(url: str) -> str:
    value = (url or '').strip()
    if re.search(r'[\s\\\x00-\x1f\x7f]', value):
        return ''
    match = _URL.fullmatch(value)
    if not match:
        return ''
    scheme, authority, path = match.groups()
    authority = authority.lower()
    port = ':443' if scheme.lower() == 'https' else ':80'
    host = authority.removesuffix(port)
    if host not in _HOSTS:
        return ''
    identifier = _PATH.fullmatch(path)
    if not identifier:
        return ''
    base, version = identifier.groups()
    if '/' in base:
        archive, number = base.split('/')
        base = archive.split('.')[0].lower() + '/' + number
    return base + (version or '')


def canonical_url(url: str) -> str:
    identifier = arxiv_id(url)
    return f'https://arxiv.org/pdf/{identifier}' if identifier else (url or '').strip().split('#', 1)[0]


def is_versioned(identifier: str) -> bool:
    return bool(re.search(r'v[1-9][0-9]*$', identifier))


def unversioned(identifier: str) -> str:
    return re.sub(r'v[1-9][0-9]*$', '', identifier)
