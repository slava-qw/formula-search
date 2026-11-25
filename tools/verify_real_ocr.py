"""Real CPU model on real, bounded paper equation crops; no mocked recognition."""
import asyncio
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.extractor.ocr_page import equation_crop
from backend.matcher.notation import notation, find_notation
from backend.ocr_runtime import shutdown_ocr
from backend.processing import run_ocr, shutdown_processing


async def main(out):
    out.mkdir(parents=True, exist_ok=True)
    cases = [
        {'paper':'2312.00752v2', 'path':'testdata/benchmarks/artifacts/2312.00752v2.pdf',
         'page':14, 'region':(367,276,406,293), 'query':'N=16', 'required':False,
         'note':'Font ambiguity diagnostic. Preserve recognition errors; require user review rather than silently correcting them.'},
        {'paper':'attention', 'path':'testdata/papers/1706.03762v7.pdf',
         'page':3, 'region':(200,454,435,493), 'query':'QK^T', 'required':True},
        {'paper':'2312.00752v2', 'path':'testdata/benchmarks/artifacts/2312.00752v2.pdf',
         'page':27, 'region':(266,92,348,108), 'query':'g_t', 'required':True},
    ]
    report = {'method':__doc__, 'cases':[]}
    try:
        for index, case in enumerate(cases):
            pdf = (ROOT/case['path']).read_bytes()
            started = time.perf_counter()
            results = await run_ocr(pdf,case['page'],case['region'])
            row = {**case,'pdf_sha256':hashlib.sha256(pdf).hexdigest(),'seconds':time.perf_counter()-started,
                   'latex':results[0].latex if results else '', 'bbox':results[0].bbox if results else None}
            row['contains_expected_notation'] = find_notation(notation(case['query']).tokens, notation(row['latex'])) is not None
            row['coordinates_preserved'] = row['bbox'] == case['region']
            report['cases'].append(row)
            print(case['paper'],row['latex'],round(row['seconds'],2),flush=True)
            (out/'real-ocr.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            # Save the exact recognized image for independent review.
            import base64
            crop = equation_crop(pdf,case['page'],case['region'])
            (out/f'ocr-crop-{index}.png').write_bytes(base64.b64decode(crop['image']))
        assert all(row['coordinates_preserved'] for row in report['cases'])
        assert all(row['contains_expected_notation'] for row in report['cases'] if row['required'])
    finally:
        shutdown_ocr()
        shutdown_processing()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'.media-work/real-ocr')
    asyncio.run(main(parser.parse_args().out))
