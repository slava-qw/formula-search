import json
from pathlib import Path

import pytest

from backend.extractor.arxiv_tex import extract_arxiv_id, is_arxiv_url
from backend.main import _normalize_url

CASES = json.loads((Path(__file__).parents[1] / 'testdata/document_identity.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=[str(i) for i in range(len(CASES))])
def test_document_identity_contract(case):
    assert is_arxiv_url(case['url']) is bool(case['id'])
    if case['id']:
        assert extract_arxiv_id(case['url']) == case['id']
    else:
        with pytest.raises(ValueError):
            extract_arxiv_id(case['url'])
    expected = case.get('canonical', case['url'].strip().split('#', 1)[0])
    assert _normalize_url(case['url']) == expected


def test_versions_and_generic_queries_are_distinct():
    assert _normalize_url('https://arxiv.org/abs/1706.03762v6') != _normalize_url('https://arxiv.org/abs/1706.03762v7')
    assert _normalize_url('https://arxiv.org/abs/1706.03762') != _normalize_url('https://arxiv.org/abs/1706.03762v7')
    assert _normalize_url('https://example.com/paper?id=1') != _normalize_url('https://example.com/paper?id=2')
