#!/usr/bin/env python3
"""Audit TeX-to-PDF formula mappings for local paper fixtures.

The script intentionally exercises the same backend path used by the extension:
load arXiv TeX, parse formula candidates, assign PDF page/bbox coordinates from
PyMuPDF's text layer, build the matcher index, and run representative searches.

Examples:
  PYTHONPATH=. python tools/audit_formula_mapping.py --all --out-dir testing/audits

  PYTHONPATH=. python tools/audit_formula_mapping.py \
    --paper attention --src testdata/papers/arXiv-1706.03762v7.tar.gz \
    --pdf testdata/papers/1706.03762v7.pdf --query 'QK^T' --query 'd_k'
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from backend.extractor.arxiv_tex import (
    _looks_like_pdf_math_line,
    _merge_tex_files,
    _norm_string,
    assign_bboxes_from_pdf,
    parse_tex_formulas,
)
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index

ROOT = Path(__file__).resolve().parents[1]
PAPER_DIR = ROOT / "testdata" / "papers"

DEFAULT_PAPERS = {
    "attention": {
        "title": "Attention Is All You Need",
        "src": PAPER_DIR / "arXiv-1706.03762v7.tar.gz",
        "pdf": PAPER_DIR / "1706.03762v7.pdf",
        "queries": [r"QK^T", r"d_k", r"\frac{1}{\sqrt{d_k}}", r"W^Q_i", r"O(1)"],
    },
    "smc_sd": {
        "title": "Faster LLM Inference via Sequential Monte Carlo",
        "src": PAPER_DIR / "arXiv-2604.15672v1.tar.gz",
        "pdf": PAPER_DIR / "2604.15672v1.pdf",
        "queries": [r"ESS", r"N(K+1)", r"\rho", r"\widehat{p}_N", r"O(1)"],
    },
    "logz": {
        "title": "Unbiased estimation of log normalizing constants",
        "src": PAPER_DIR / "arXiv-1810.01382v1.tar.gz",
        "pdf": PAPER_DIR / "1810.01382v1.pdf",
        "queries": [r"\pi_\lambda", r"Z_\lambda", r"\hat r_{01}", r"\nabla_\lambda", r"q(\lambda)"],
    },
    # Longer, more equation-dense theory papers used to stress-test
    # preprocessing speed on harder documents than the three fixtures above.
    "vae_tutorial": {
        "title": "Tutorial on Variational Autoencoders",
        "src": PAPER_DIR / "arXiv-1606.05908.tar.gz",
        "pdf": PAPER_DIR / "1606.05908.pdf",
        "queries": [r"D_{KL}", r"\mathcal{L}", r"p_\theta", r"q_\phi", r"\epsilon"],
    },
    "gan_tutorial": {
        "title": "NIPS 2016 Tutorial: Generative Adversarial Networks",
        "src": PAPER_DIR / "arXiv-1701.00160.tar.gz",
        "pdf": PAPER_DIR / "1701.00160.pdf",
        "queries": [r"p_{data}", r"p_g", r"\mathbb{E}", r"D(x)", r"\nabla_\theta"],
    },
}


def _load_tex_files(path: Path) -> dict[str, str]:
    if path.is_dir():
        return {str(p.relative_to(path)): p.read_text(errors="replace") for p in path.rglob("*.tex")}
    if path.name.endswith(".tar.gz"):
        result: dict[str, str] = {}
        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith(".tex"):
                    fh = tar.extractfile(member)
                    if fh:
                        result[member.name] = fh.read().decode("utf-8", errors="replace")
        return result
    if path.suffix == ".tex":
        return {path.name: path.read_text(errors="replace")}
    raise ValueError(f"Unsupported source path: {path}")


def _bbox_key(bbox):
    return tuple(round(float(v), 1) for v in bbox) if bbox else None


def _clip(text: str, limit: int = 360) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …"


def _candidate_summary(c, index: int) -> dict:
    return {
        "index": index,
        "latex": _clip(c.latex),
        "source": c.source,
        "env": c.env,
        "section": c.section,
        "page": c.page,
        "page_human": c.page + 1 if c.page >= 0 else None,
        "bbox": list(c.bbox) if c.bbox else None,
        "bbox_quality": c.bbox_quality,
        "pdf_line_text": _clip(c.pdf_line_text),
        "pdf_line_group_kind": c.pdf_line_group_kind,
    }


def audit_paper(name: str, src: Path, pdf: Path, queries: Iterable[str] = (), *, title: str = "") -> dict:
    timings: dict[str, float] = {}

    def timed(label: str, fn):
        start = time.perf_counter()
        value = fn()
        timings[label] = round(time.perf_counter() - start, 4)
        return value

    print(f"[audit:{name}] loading TeX", file=sys.stderr, flush=True)
    files = timed("load_tex_archive_sec", lambda: _load_tex_files(src))
    tex = timed("merge_tex_sec", lambda: _merge_tex_files(files))
    print(f"[audit:{name}] parsing formulas", file=sys.stderr, flush=True)
    candidates = timed("parse_tex_sec", lambda: parse_tex_formulas(tex))
    print(f"[audit:{name}] assigning PDF bboxes for {len(candidates)} candidates", file=sys.stderr, flush=True)
    pdf_bytes = timed("read_pdf_sec", lambda: pdf.read_bytes())
    timed("assign_bboxes_sec", lambda: assign_bboxes_from_pdf(candidates, pdf_bytes, progress=True))
    print(f"[audit:{name}] scoring diagnostics", file=sys.stderr, flush=True)

    duplicate_groups = defaultdict(list)
    for idx, c in enumerate(candidates):
        duplicate_groups[(c.latex.strip(), c.page, _bbox_key(c.bbox))].append(idx)
    duplicates = [items for items in duplicate_groups.values() if len(items) > 1]

    suspicious = []
    for idx, c in enumerate(candidates):
        if c.bbox is None:
            continue
        norm = _norm_string(c.latex, is_latex=True)
        line = c.pdf_line_text or ""
        if len(norm) <= 8 and line and not _looks_like_pdf_math_line(line) and c.pdf_line_math_fraction <= 0:
            suspicious.append(_candidate_summary(c, idx))

    index = timed("build_index_sec", lambda: build_index(candidates))
    query_results = {}
    query_timings = {}
    for query in queries:
        print(f"[audit:{name}] query {query!r}", file=sys.stderr, flush=True)
        q_start = time.perf_counter()
        hits = search_formulas(query, index)[:25]
        query_timings[query] = round(time.perf_counter() - q_start, 4)
        query_results[query] = [
            {
                "score": r.score,
                "match_type": r.match_type,
                "page": r.page,
                "page_human": r.page + 1 if r.page >= 0 else None,
                "bbox": list(r.bbox) if r.bbox else None,
                "bbox_source": r.bbox_source,
                "latex": _clip(r.latex, 520),
                "section": r.section,
            }
            for r in hits
        ]

    unmapped_bbox = [_candidate_summary(c, i) for i, c in enumerate(candidates) if c.bbox is None]
    duplicate_examples = []
    for items in duplicates[:40]:
        duplicate_examples.append([_candidate_summary(candidates[i], i) for i in items])

    return {
        "paper": name,
        "title": title,
        "src": str(src),
        "pdf": str(pdf),
        "tex_files": len(files),
        "formula_count": len(candidates),
        "page_mapped": sum(1 for c in candidates if c.page >= 0),
        "bbox_mapped": sum(1 for c in candidates if c.bbox is not None),
        "unmapped_bbox_count": len(unmapped_bbox),
        "envs": dict(Counter(c.env for c in candidates)),
        "bbox_quality": dict(Counter(c.bbox_quality or "none" for c in candidates)),
        "duplicate_group_count": len(duplicates),
        "duplicate_item_count": sum(len(items) for items in duplicates),
        "duplicate_examples": duplicate_examples,
        "suspicious_short_non_math_count": len(suspicious),
        "suspicious_short_non_math_examples": suspicious[:80],
        "unmapped_bbox_examples": unmapped_bbox[:120],
        "queries": query_results,
        "timings_sec": timings,
        "query_timings_sec": query_timings,
    }


def _write_report(result: dict, out_dir: Path | None, json_out: Path | None = None, md_out: Path | None = None) -> None:
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_out = out_dir / f"{result['paper']}.json"
        md_out = out_dir / f"{result['paper']}.md"

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(text, encoding="utf-8")
    if md_out:
        md_out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Formula mapping audit: {result['paper']}",
            "",
            f"Paper: {result.get('title') or result['paper']}",
            "",
            f"- Formula count: {result['formula_count']}",
            f"- Page mapped: {result['page_mapped']}",
            f"- BBox mapped: {result['bbox_mapped']}",
            f"- BBox-unmapped/page-only candidates: {result['unmapped_bbox_count']}",
            f"- Duplicate visual groups: {result['duplicate_group_count']} ({result['duplicate_item_count']} candidate items)",
            f"- Suspicious short non-math mappings: {result['suspicious_short_non_math_count']}",
            "",
            "## Timing",
        ]
        for label, value in result.get("timings_sec", {}).items():
            lines.append(f"- {label}: {value:.4f}s")
        lines.extend([
            "",
            "## Query probes",
        ])
        for q, hits in result["queries"].items():
            lines.append(f"\n### `{q}`")
            if not hits:
                lines.append("No hits.")
            for hit in hits[:12]:
                page = hit["page_human"] if hit["page_human"] is not None else "unmapped"
                bbox = "bbox" if hit["bbox"] else "no-bbox"
                lines.append(f"- p. {page}, {hit['match_type']}, {hit['bbox_source']}, {bbox}: `{hit['latex'][:180]}`")
        if result["unmapped_bbox_examples"]:
            lines.append("\n## First bbox-unmapped/page-only candidates")
            for item in result["unmapped_bbox_examples"][:30]:
                page = item["page_human"] if item["page_human"] is not None else "unmapped"
                lines.append(f"- #{item['index']} p. {page} `{item['latex'][:160]}`")
        md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="audit all bundled paper fixtures")
    parser.add_argument("--out-dir", type=Path, help="directory for <paper>.json and <paper>.md outputs")
    parser.add_argument("--paper", help="paper name for a single audit")
    parser.add_argument("--src", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()

    if args.all:
        for name, spec in DEFAULT_PAPERS.items():
            queries = args.query or spec["queries"]
            result = audit_paper(name, spec["src"], spec["pdf"], queries, title=spec["title"])
            _write_report(result, args.out_dir)
        return

    if not args.paper:
        parser.error("--paper is required unless --all is used")
    if not args.src or not args.pdf:
        if args.paper in DEFAULT_PAPERS:
            spec = DEFAULT_PAPERS[args.paper]
            args.src = args.src or spec["src"]
            args.pdf = args.pdf or spec["pdf"]
            if not args.query:
                args.query = list(spec["queries"])
        else:
            parser.error("--src and --pdf are required for custom paper names")

    result = audit_paper(args.paper, args.src, args.pdf, args.query, title=DEFAULT_PAPERS.get(args.paper, {}).get("title", ""))
    _write_report(result, args.out_dir, args.json_out, args.md_out)


if __name__ == "__main__":
    main()
