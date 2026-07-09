# -*- coding: utf-8 -*-
"""Unit tests for `modules/takeoff_vector.py` — deterministic vector-tag counter.

Two tiers:
  * PURE tests feed hand-built strings / EdgePrimitives straight into the pure
    layer (count_penetration_tags / measure_edges / crosscheck). No PDF, no
    third-party libs, fully deterministic.
  * INTEGRATION tests synthesize a tiny plan PDF with reportlab (known tags,
    a 100x50-pt rectangle of 4 line segments, and a FreeText annotation), then
    run scan_pdf over it and assert the extracted counts/lengths. These skip
    cleanly if reportlab/pdfplumber/pypdf aren't installed.

Run standalone (repo convention):
    cd whitelabel-crm
    python tests/test_takeoff_vector.py
Or under pytest:
    pytest tests/test_takeoff_vector.py -q
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest import SkipTest

# UTF-8 stdout so box-drawing chars in project docstrings don't crash reporting
# on Windows cp1252 terminals (matches sibling tests).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Make the project root importable when run as a script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
from modules import takeoff_vector as tv  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# PURE LAYER
# ─────────────────────────────────────────────────────────────────────────────
def test_count_basic_categories():
    labels = ["VTR VTR RD", "SKYLIGHT", "EXHAUST FAN", "BOARD", "R.D.", "V.T.R."]
    tc = tv.count_penetration_tags(labels)
    assert tc.by_category["plumbing_vent"] == 3, tc.by_category   # 2x VTR + V.T.R.
    assert tc.by_category["roof_drain"] == 2, tc.by_category       # RD + R.D.
    assert tc.by_category["skylight"] == 1, tc.by_category
    assert tc.by_category["exhaust_fan"] == 1, tc.by_category
    assert tc.total == 7, tc.by_category
    assert tc.nonzero() == {
        "plumbing_vent": 3, "roof_drain": 2, "skylight": 1, "exhaust_fan": 1,
    }, tc.nonzero()
    print("  [PASS] count_basic_categories")


def test_count_no_false_positives_on_word_boundaries():
    # 'RD' inside BOARD/HARDWARE and 'EF' inside REF/PREFAB must NOT match.
    labels = ["BOARD", "HARDWARE", "REFRIGERATOR", "PREFAB", "CHIMNEY"]
    tc = tv.count_penetration_tags(labels)
    assert tc.total == 0, f"expected 0, got {tc.by_category}"
    print("  [PASS] count_no_false_positives_on_word_boundaries")


def test_count_empty_and_none_labels_ignored():
    tc = tv.count_penetration_tags(["", None, "   ", "VTR"])  # type: ignore[list-item]
    assert tc.by_category["plumbing_vent"] == 1
    assert tc.total == 1
    print("  [PASS] count_empty_and_none_labels_ignored")


def test_count_custom_patterns():
    patterns = {"solar": r"SOLAR\s+PANEL|PV"}
    tc = tv.count_penetration_tags(["PV PV", "SOLAR PANEL", "BOARD"], patterns)
    assert set(tc.by_category) == {"solar"}, tc.by_category
    assert tc.by_category["solar"] == 3, tc.by_category   # 2x PV + SOLAR PANEL
    assert tc.total == 3
    print("  [PASS] count_custom_patterns")


def test_count_dotted_acronym_not_over_consumed():
    # 'VTR' immediately abutting another letter should be boundary-blocked.
    tc = tv.count_penetration_tags(["XVTRX", "VTR-1"])
    # 'XVTRX' blocked (letters both sides); 'VTR-1' ok (dash boundary).
    assert tc.by_category["plumbing_vent"] == 1, tc.by_category
    print("  [PASS] count_dotted_acronym_not_over_consumed")


def test_edge_primitive_length():
    assert tv.EdgePrimitive(0, 0, 3, 4).length == 5.0
    assert tv.EdgePrimitive(10, 10, 10, 10).length == 0.0
    print("  [PASS] edge_primitive_length")


def test_measure_edges_rectangle():
    # 100x50 rectangle → perimeter 300 pt.
    edges = [
        tv.EdgePrimitive(100, 300, 200, 300, page=1),  # 100
        tv.EdgePrimitive(200, 300, 200, 350, page=1),  # 50
        tv.EdgePrimitive(200, 350, 100, 350, page=1),  # 100
        tv.EdgePrimitive(100, 350, 100, 300, page=1),  # 50
    ]
    m = tv.measure_edges(edges, scale_ft_per_pt=0.5)
    assert m.count == 4
    assert abs(m.total_length_pt - 300.0) < 1e-9, m.total_length_pt
    assert abs(m.total_length_ft - 150.0) < 1e-9, m.total_length_ft
    assert m.per_page_length_pt == {1: 300.0}
    print("  [PASS] measure_edges_rectangle")


def test_measure_edges_min_length_filter():
    edges = [
        tv.EdgePrimitive(0, 0, 100, 0, page=1),   # 100 kept
        tv.EdgePrimitive(5, 5, 5, 5, page=1),     # 0   dropped
        tv.EdgePrimitive(0, 0, 0, 0.4, page=2),   # 0.4 dropped by min 1.0
    ]
    m = tv.measure_edges(edges, min_length_pt=1.0)
    assert m.count == 1, m.count
    assert abs(m.total_length_pt - 100.0) < 1e-9
    assert m.per_page_length_pt == {1: 100.0}
    assert m.total_length_ft is None  # no scale given
    print("  [PASS] measure_edges_min_length_filter")


def test_measure_edges_empty():
    m = tv.measure_edges([])
    assert m.count == 0
    assert m.total_length_pt == 0.0
    assert m.per_page_length_pt == {}
    print("  [PASS] measure_edges_empty")


def test_crosscheck_abs_and_pct():
    c1 = tv.crosscheck("vents", 12, 10, abs_tol=1)
    assert c1.delta == 2 and c1.within_tolerance is False
    assert abs(c1.pct_delta - 0.2) < 1e-9

    c2 = tv.crosscheck("vents", 12, 10, abs_tol=1, pct_tol=0.25)
    assert c2.within_tolerance is True  # 20% within 25%

    c3 = tv.crosscheck("eave_lf", 5, 5)
    assert c3.delta == 0 and c3.within_tolerance is True

    # ai_value == 0 → no pct, only abs bound applies.
    c4 = tv.crosscheck("x", 3, 0, abs_tol=0)
    assert c4.pct_delta is None and c4.within_tolerance is False
    c5 = tv.crosscheck("x", 0, 0)
    assert c5.pct_delta is None and c5.within_tolerance is True
    print("  [PASS] crosscheck_abs_and_pct")


def test_vectorscan_helpers_pure():
    scan = tv.VectorScan(
        labels=["VTR", "RD"],
        edges=[tv.EdgePrimitive(0, 0, 30, 40, page=1)],  # length 50
        annotations=[tv.Annotation(subtype="FreeText", contents="PIPE BOOT", page=1)],
        page_count=1,
    )
    assert scan.has_vector_data is True
    # annotation contents fold into the counted text.
    tc = scan.penetration_tags()
    assert tc.by_category["plumbing_vent"] == 1
    assert tc.by_category["roof_drain"] == 1
    assert tc.by_category["pipe_boot"] == 1
    assert tc.total == 3, tc.by_category
    m = scan.edge_measure(scale_ft_per_pt=2.0)
    assert abs(m.total_length_pt - 50.0) < 1e-9
    assert abs(m.total_length_ft - 100.0) < 1e-9
    # empty scan → no vector data
    assert tv.VectorScan().has_vector_data is False
    print("  [PASS] vectorscan_helpers_pure")


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION LAYER (reportlab → scan_pdf)
# ─────────────────────────────────────────────────────────────────────────────
def _require(modname: str):
    try:
        __import__(modname)
    except ImportError:
        raise SkipTest(f"{modname} not installed")


def _make_plan_pdf(with_annotation: bool = False) -> bytes:
    """Synthesize a minimal plan PDF: known tags + a 100x50-pt rectangle of 4
    line segments, optionally a FreeText annotation reading 'PIPE BOOT'."""
    _require("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    # Wide x spacing (100/200/300) so pdfplumber keeps them as separate words.
    c.drawString(100, 700, "VTR")
    c.drawString(200, 700, "VTR")
    c.drawString(300, 700, "RD")
    c.drawString(100, 670, "SKYLIGHT")
    c.drawString(100, 640, "EXHAUST FAN")
    c.drawString(100, 610, "BOARD")  # false-positive guard (contains 'RD')
    # 100x50 rectangle drawn as 4 explicit line segments.
    pts = [(100, 300), (200, 300), (200, 350), (100, 350), (100, 300)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        c.line(x0, y0, x1, y1)
    c.showPage()
    c.save()
    data = buf.getvalue()

    if with_annotation:
        _require("pypdf")
        from pypdf import PdfReader, PdfWriter
        from pypdf.annotations import FreeText

        reader = PdfReader(io.BytesIO(data))
        writer = PdfWriter()
        writer.append(reader)
        writer.add_annotation(
            page_number=0,
            annotation=FreeText(text="PIPE BOOT", rect=(50, 500, 250, 540)),
        )
        out = io.BytesIO()
        writer.write(out)
        data = out.getvalue()
    return data


def test_scan_pdf_text_and_edges():
    _require("pdfplumber")
    _require("pypdf")
    data = _make_plan_pdf(with_annotation=False)
    scan = tv.scan_pdf(data)
    assert scan.page_count == 1
    assert scan.has_vector_data is True

    tc = scan.penetration_tags()
    assert tc.by_category["plumbing_vent"] == 2, tc.by_category
    assert tc.by_category["roof_drain"] == 1, tc.by_category
    assert tc.by_category["skylight"] == 1, tc.by_category
    assert tc.by_category["exhaust_fan"] == 1, tc.by_category
    assert tc.total == 5, tc.by_category

    m = scan.edge_measure(scale_ft_per_pt=0.1)
    assert m.count == 4, m.count
    assert abs(m.total_length_pt - 300.0) < 0.5, m.total_length_pt
    assert abs(m.total_length_ft - 30.0) < 0.05, m.total_length_ft
    print("  [PASS] scan_pdf_text_and_edges")


def test_scan_pdf_from_path_and_bytes_match():
    _require("pdfplumber")
    _require("pypdf")
    data = _make_plan_pdf(with_annotation=False)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        from_bytes = tv.scan_pdf(data)
        from_path = tv.scan_pdf(path)
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass
    assert from_bytes.labels == from_path.labels
    assert from_bytes.penetration_tags().by_category == \
        from_path.penetration_tags().by_category
    assert len(from_bytes.edges) == len(from_path.edges)
    print("  [PASS] scan_pdf_from_path_and_bytes_match")


def test_scan_pdf_annotation_extracted():
    _require("pdfplumber")
    _require("pypdf")
    data = _make_plan_pdf(with_annotation=True)
    scan = tv.scan_pdf(data)
    subtypes = [a.subtype for a in scan.annotations]
    contents = [a.contents for a in scan.annotations]
    assert "FreeText" in subtypes, subtypes
    assert any("PIPE BOOT" in ct for ct in contents), contents
    # Annotation contents fold into penetration counting → pipe_boot >= 1.
    tc = scan.penetration_tags()
    assert tc.by_category["pipe_boot"] >= 1, tc.by_category
    print("  [PASS] scan_pdf_annotation_extracted")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner (mirrors sibling tests' __main__ convention)
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    tests = [
        test_count_basic_categories,
        test_count_no_false_positives_on_word_boundaries,
        test_count_empty_and_none_labels_ignored,
        test_count_custom_patterns,
        test_count_dotted_acronym_not_over_consumed,
        test_edge_primitive_length,
        test_measure_edges_rectangle,
        test_measure_edges_min_length_filter,
        test_measure_edges_empty,
        test_crosscheck_abs_and_pct,
        test_vectorscan_helpers_pure,
        test_scan_pdf_text_and_edges,
        test_scan_pdf_from_path_and_bytes_match,
        test_scan_pdf_annotation_extracted,
    ]
    failures = 0
    skipped = 0
    for fn in tests:
        try:
            fn()
        except SkipTest as e:
            skipped += 1
            print(f"  [SKIP] {fn.__name__}: {e}")
        except AssertionError as e:
            failures += 1
            print(f"  [FAIL] {fn.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failures += 1
            print(f"  [ERR ] {fn.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"{len(tests)} tests, {failures} failed, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
