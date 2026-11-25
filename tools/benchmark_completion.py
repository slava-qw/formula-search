"""Local HTTP preparation/search/restart benchmark with pinned paper bytes.

Includes archive processing, spawned PDF work, index construction, response JSON
and mapping-cache writes. Excludes remote downloads and Chrome rendering.
Compare all returned matches after restart, not just counts or rectangles.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from backend import cache, main, processing
from backend.sessions import SessionStore
from backend.extractor import arxiv_tex as tex


async def run(out):
    out.mkdir(parents=True, exist_ok=True)
    cache.CACHE_DIR = Path(tempfile.mkdtemp(prefix='formula-completion-cache-'))
    probes = json.loads((ROOT/'testdata/benchmarks/source_inline_probes.json').read_text())['papers']
    report = {'method': __doc__, 'cache_directory': str(cache.CACHE_DIR), 'papers': []}
    originals = tex.fetch_tex_source, tex._fetch_pdf_bytes
    try:
        for paper in ['attention', '2307.09288v2', '1802.01528v3', '2312.00752v2']:
            probe = next(p for p in probes if p['paper'] == paper)
            pdf, source = ((ROOT/probe[key]).read_bytes() for key in ('pdf_path', 'source_path'))
            assert hashlib.sha256(pdf).hexdigest() == probe['hashes']['pdf']
            assert hashlib.sha256(source).hexdigest() == probe['hashes']['source']
            identifier = '1706.03762v7' if paper == 'attention' else paper
            url = f'https://arxiv.org/pdf/{identifier}'
            async def source_bytes(_):
                return await processing.run_document_job(tex._unpack_tex_source, source)
            async def pdf_bytes(_):
                return pdf
            tex.fetch_tex_source, tex._fetch_pdf_bytes = source_bytes, pdf_bytes
            main._sessions = SessionStore()
            main._sessions_lock = main._sessions.lock
            processing.shutdown_processing()
            row = {'paper': paper, 'hashes': probe['hashes'], 'runs': []}
            expected = None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://local') as client:
                for mode in ['cold', 'resident', 'restart']:
                    if mode == 'restart':
                        main._sessions = SessionStore()
                        main._sessions_lock = main._sessions.lock
                        processing.shutdown_processing()
                    started = time.perf_counter()
                    response = await client.post('/process', data={'url':url})
                    response.raise_for_status()
                    prepare_ms = (time.perf_counter()-started)*1000
                    started = time.perf_counter()
                    result = await client.post('/search', json={'url':url, 'query':'QK^T' if paper == 'attention' else 'x'})
                    result.raise_for_status()
                    wall_ms = (time.perf_counter()-started)*1000
                    data = result.json()
                    if expected is None:
                        expected = data['matches']
                    assert expected == data['matches'], (paper, mode)
                    session = main._sessions[url]
                    row['runs'].append({'mode':mode, 'prepare_ms':prepare_ms, 'search_http_ms':wall_ms,
                                        'search_backend_ms':data['search_ms'], 'matches':len(data['matches']),
                                        'persisted_mappings':sum(bool(c.bbox_quality) for c in session.candidates_cache),
                                        'process':response.json()})
                    print(paper, mode, round(prepare_ms,1), round(wall_ms,1), flush=True)
            row['match_sha256'] = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
            report['papers'].append(row)
            (out/'http-restart.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    finally:
        tex.fetch_tex_source, tex._fetch_pdf_bytes = originals
        processing.shutdown_processing()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(run(parser.parse_args().out))
