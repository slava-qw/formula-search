"""Query PyPI's advisory records for pinned package versions (network required).

Only package names and versions are sent. This is a package advisory inventory,
not an exploitability assessment or a proof that the application is secure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import httpx

ROOT=Path(__file__).resolve().parents[1]


def audit(requirements, output):
    records, errors = [], []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for line in requirements.read_text(encoding='utf-8').splitlines():
            match=re.fullmatch(r'([\w.-]+)==([^\s;]+)',line.strip())
            if not match:
                continue
            name,version=match.groups()
            try:
                # PyTorch CPU wheels use a local +cpu suffix absent from PyPI.
                # Query the corresponding upstream release's advisory metadata.
                lookup_version=version.split('+',1)[0]
                response=client.get(f'https://pypi.org/pypi/{name}/{lookup_version}/json')
                response.raise_for_status()
                items=response.json().get('vulnerabilities',[])
                advisories=[{'id':item['id'],'aliases':item.get('aliases',[]),
                             'fixed_in':item.get('fixed_in',[]),'link':item.get('link')}
                            for item in items if not item.get('withdrawn')]
                records.append({'package':name,'version':version,'lookup_version':lookup_version,'advisories':advisories})
            except (httpx.HTTPError,ValueError) as error:
                errors.append({'package':name,'version':version,'error':str(error)})
    report={'checked_utc':datetime.now(timezone.utc).isoformat(),
        'source':'PyPI version JSON API, vulnerabilities field',
        'requirements':requirements.name,
        'scope':'Pinned package advisories; duplicate aliases may describe the same vulnerability. Not an application exploitability audit.',
        'packages_checked':len(records),'packages_with_advisories':sum(bool(r['advisories']) for r in records),
        'errors':errors,'packages':records}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='packages'},indent=2))
    for record in records:
        if record['advisories']:
            print(record['package'],record['version'],len(record['advisories']),'advisory records')
    return bool(errors or report['packages_with_advisories'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requirements',type=Path,default=ROOT/'requirements-core-lock.txt')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(audit(args.requirements,args.out))
