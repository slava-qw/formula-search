"""Regression checks for bundled multi-paper formula mapping audits.

The default audit tests validate the cached reports committed under testing/audits so
normal test runs are fast and deterministic.  Set RUN_PAPER_AUDITS_RECOMPUTE=1 to
force a fresh end-to-end rebuild from the bundled PDFs and arXiv source archives.
"""

import json
import os
from pathlib import Path

import pytest

from tools.audit_formula_mapping import DEFAULT_PAPERS, audit_paper

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "testing" / "audits"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PAPER_AUDITS") != "1",
    reason="set RUN_PAPER_AUDITS=1 to run bundled paper audit regressions",
)


def _report(name: str) -> dict:
    path = AUDIT_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip('Historical cached reports are omitted from the public snapshot; use recompute gates.')
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_no_unverified_partial_cards(report: dict) -> None:
    # Source retrieval and localization are independent: an unlocated partial
    # match is legitimate, but must not draw a rectangle claiming a subspan.
    for query, hits in report["queries"].items():
        assert isinstance(hits, list), query
        for hit in hits:
            assert not (hit.get("bbox_source") == "query-subspan-missing" and hit["bbox"] is not None), (
                report["name"],
                query,
                hit.get("latex", "")[:120],
            )


def test_cached_reports_have_expected_mapping_metrics():
    expected = {
        "attention": (100, 90),
        "smc_sd": (400, 300),
        "logz": (600, 560),
    }
    for name, (min_formulas, min_bbox) in expected.items():
        report = _report(name)
        assert report["formula_count"] >= min_formulas
        assert report["bbox_mapped"] >= min_bbox
        _assert_no_unverified_partial_cards(report)


def test_attention_cached_query_probes_are_precise():
    report = _report("attention")

    qkt = report["queries"][r"QK^T"]
    assert len(qkt) == 1
    assert qkt[0]["page"] == 3
    assert qkt[0]["bbox"] is not None
    assert qkt[0]["bbox_source"] == "query-subspan"

    dk_hits = report["queries"][r"d_k"]
    assert dk_hits
    visual_keys = {
        (
            hit["page"],
            tuple(round(float(v), 1) for v in hit["bbox"]) if hit["bbox"] else None,
            hit["bbox_source"],
        )
        for hit in dk_hits
    }
    assert len(visual_keys) == len(dk_hits)
    assert all(hit["bbox"] is not None for hit in dk_hits if hit["bbox_source"] == "query-subspan")


def test_smc_cached_queries_do_not_return_wrong_unverified_cards():
    report = _report("smc_sd")

    nk_hits = report["queries"][r"N(K+1)"]
    assert len(nk_hits) == 1
    assert nk_hits[0]["bbox"] is not None
    assert nk_hits[0]["bbox_source"] in {"query-subspan", "tex-candidate"}

    for query in [r"ESS", r"\widehat{p}_N", r"\rho"]:
        hits = report["queries"][query]
        assert hits, query
        assert all(hit["bbox"] is not None for hit in hits if hit["match_type"] != "exact")


def test_logz_cached_macros_and_accented_queries_are_precise():
    report = _report("logz")

    pi_hits = report["queries"][r"\pi_\lambda"]
    assert pi_hits
    assert pi_hits[0]["bbox"] is not None
    assert pi_hits[0]["bbox_source"] == "query-subspan"

    hat_hits = report["queries"][r"\hat r_{01}"]
    assert hat_hits
    assert all(r"\hat" in hit["latex"] for hit in hat_hits[:5])
    assert all(hit["bbox"] is not None for hit in hat_hits[:5])


@pytest.mark.skipif(
    os.environ.get("RUN_PAPER_AUDITS_RECOMPUTE") != "1",
    reason="set RUN_PAPER_AUDITS_RECOMPUTE=1 to recompute the heavy bundled-paper audits",
)
def test_recompute_bundled_paper_audits():
    minimum_bboxes = {
        "attention": 90, "smc_sd": 300, "logz": 560,
        "vae_tutorial": 220, "gan_tutorial": 130,
    }
    for name, spec in DEFAULT_PAPERS.items():
        report = audit_paper(name, spec["src"], spec["pdf"], spec["queries"], title=spec["title"])
        assert report["bbox_mapped"] >= minimum_bboxes[name]
        _assert_no_unverified_partial_cards(report)
