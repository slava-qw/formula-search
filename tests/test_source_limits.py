import gzip
import io
import tarfile

import pytest

from backend.extractor import arxiv_tex as arxiv


def archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, content in files:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_tex_file_and_total_limits(monkeypatch):
    monkeypatch.setattr(arxiv, 'MAX_TEX_FILE_BYTES', 10)
    with pytest.raises(ValueError, match='limit'):
        arxiv._unpack_tex_source(archive([('large.tex', b'x' * 11)]))
    monkeypatch.setattr(arxiv, 'MAX_TEX_TOTAL_BYTES', 15)
    with pytest.raises(ValueError, match='limit'):
        arxiv._unpack_tex_source(archive([('a.tex', b'x' * 8), ('b.tex', b'x' * 8)]))


def test_compressed_non_tex_data_and_member_count_are_bounded(monkeypatch):
    monkeypatch.setattr(arxiv, 'MAX_SOURCE_BYTES', 1024)
    with pytest.raises(ValueError, match='limit'):
        arxiv._unpack_tex_source(archive([('large.bin', b'x' * 20000)]))
    monkeypatch.setattr(arxiv, 'MAX_SOURCE_BYTES', 100000)
    monkeypatch.setattr(arxiv, 'MAX_SOURCE_MEMBERS', 2)
    with pytest.raises(ValueError, match='members'):
        arxiv._unpack_tex_source(archive([('a.bin', b'a'), ('b.bin', b'b'), ('c.bin', b'c')]))


def test_single_gzipped_tex_is_supported_and_bounded(monkeypatch):
    assert '$x^2$' in arxiv._unpack_tex_source(gzip.compress(b'$x^2$'))
    monkeypatch.setattr(arxiv, 'MAX_TEX_FILE_BYTES', 10)
    with pytest.raises(ValueError, match='limit'):
        arxiv._unpack_tex_source(gzip.compress(b'x' * 11))


def test_plain_bad_archive_is_reported():
    with pytest.raises(ValueError, match='Archive'):
        arxiv._unpack_tex_source(b'not a source archive')
