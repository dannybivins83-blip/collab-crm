# -*- coding: utf-8 -*-
"""Deterministic vector-tag counter for roof plan PDFs — an AI cross-check.

The estimator/roof-report pipeline produces roof-penetration counts and
edge (eave/ridge/rake/valley) linear footage using an LLM reading the plans.
That is fast but non-deterministic. This module gives us a *second, purely
mechanical opinion* by extracting the PDF's own **vector text**, **vector line
primitives**, and **annotations** and counting/measuring them with plain regex
and geometry. No AI, no network, no database — just the bytes on the page.

Use it to answer: "the AI says 7 plumbing vents and 240 LF of eave — does the
vector geometry of the drawing corroborate that, or is it way off?"

Design (so it is trivially unit-testable and safe to import anywhere):

  * The **pure** layer (`count_penetration_tags`, `measure_edges`, `crosscheck`,
    the dataclasses) has ZERO third-party dependencies. You feed it plain
    strings and numbers. Every unit test hits this layer directly.
  * The **extraction** layer (`scan_pdf`) lazily imports `pdfplumber` (text +
    line primitives) and `pypdf` (annotations) only when actually called.

IMPORTANT: this is a *cross-check heuristic*, never ground truth. Text-based
tag counting will over-count if a legend/key spells the tag out, and will
under-count on raster (scanned) plans that carry no vector text. Vector edge
length sums every stroked line, including title-block rules and leader lines,
so it needs a known drawing scale and sensible min-length filtering before the
number means anything. Treat disagreement as "a human should look," not "the AI
is wrong." This module is intentionally NOT wired into the live worker.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Union

__all__ = [
    "EdgePrimitive",
    "Annotation",
    "TagCount",
    "EdgeMeasure",
    "CrossCheck",
    "VectorScan",
    "DEFAULT_PENETRATION_PATTERNS",
    "compile_patterns",
    "count_penetration_tags",
    "measure_edges",
    "crosscheck",
    "scan_pdf",
]

# ─────────────────────────────────────────────────────────────────────────────
# Penetration tag vocabulary
#
# Each category maps to a raw regex body of alternatives, ordered LONGEST-first
# so that e.g. "EXHAUST FAN" is consumed as one match rather than counted twice
# ("EXHAUST FAN" + "EXHAUST"). At compile time the whole body is wrapped in
# letter-boundary lookarounds — `(?<![A-Za-z]) ... (?![A-Za-z])` — instead of
# `\b`, because `\b` mis-handles dotted acronyms like "V.T.R." (the trailing
# "." kills the word boundary). Lookarounds also stop "RD" matching inside
# "BOARD" and "EF" inside "REF".
#
# Categories are deliberately distinct substrings so a single tag never lands in
# two categories. Matching is case-insensitive.
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_PENETRATION_PATTERNS: Dict[str, str] = {
    "plumbing_vent": r"VENT\s+THROUGH\s+ROOF|PLUMBING\s+VENT|PLBG\s+VENT|V\.?T\.?R\.?",
    "roof_drain":    r"ROOF\s+DRAIN|R\.?D\.?",
    "overflow_drain": r"OVERFLOW\s+DRAIN|O\.?F\.?D\.?|O\.?D\.?",
    "skylight":      r"SKYLIGHT|SKYLT",
    "exhaust_fan":   r"EXHAUST\s+FAN|EXHAUST|EXH\s+FAN|EF",
    "gas_flue":      r"GAS\s+VENT|B[-\s]?VENT|FLUE",
    "rooftop_unit":  r"ROOFTOP\s+UNIT|RTU|HVAC\s+CURB",
    "pipe_boot":     r"PIPE\s+BOOT|PIPE\s+FLASHING|PIPE\s+JACK",
}


def compile_patterns(patterns: Optional[Mapping[str, str]] = None) -> "Dict[str, re.Pattern]":
    """Compile a category→raw-alternation mapping into category→compiled regex.

    Each body is wrapped in letter-boundary lookarounds and compiled
    case-insensitively. Insertion order of the mapping is preserved.
    """
    src = patterns if patterns is not None else DEFAULT_PENETRATION_PATTERNS
    compiled: Dict[str, re.Pattern] = {}
    for category, body in src.items():
        compiled[category] = re.compile(
            r"(?<![A-Za-z])(?:" + body + r")(?![A-Za-z])", re.IGNORECASE
        )
    return compiled


# Compiled once for the default vocabulary (the common path).
_DEFAULT_COMPILED = compile_patterns(DEFAULT_PENETRATION_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# Value types (all frozen → hashable, safe to reuse across calls)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EdgePrimitive:
    """A single stroked vector line segment in PDF user-space points.

    Coordinates are the pdfplumber `x0,y0,x1,y1` PDF-space values (origin
    bottom-left, y increasing upward). `page` is 1-indexed.
    """
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 1

    @property
    def length(self) -> float:
        """Euclidean length of the segment, in PDF points (1/72 inch)."""
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass(frozen=True)
class Annotation:
    """A PDF annotation (reviewer markup / callout).

    `subtype` is the PDF subtype without the leading slash (e.g. "FreeText",
    "Square", "Text"). `contents` is the annotation's text, possibly empty.
    """
    subtype: str
    contents: str
    page: int = 1


@dataclass(frozen=True)
class TagCount:
    """Result of counting roof-penetration tags."""
    by_category: Dict[str, int]
    total: int

    def nonzero(self) -> Dict[str, int]:
        """Just the categories with at least one hit (stable order)."""
        return {k: v for k, v in self.by_category.items() if v}


@dataclass(frozen=True)
class EdgeMeasure:
    """Result of summing vector edge primitives."""
    total_length_pt: float
    per_page_length_pt: Dict[int, float]
    count: int
    scale_ft_per_pt: Optional[float] = None
    total_length_ft: Optional[float] = None


@dataclass(frozen=True)
class CrossCheck:
    """One vector-vs-AI comparison for a single quantity."""
    field: str
    vector_value: float
    ai_value: float
    delta: float                # vector - ai
    pct_delta: Optional[float]  # delta / ai, or None if ai == 0
    within_tolerance: bool


# ─────────────────────────────────────────────────────────────────────────────
# PURE LAYER — no third-party imports below use anything but stdlib
# ─────────────────────────────────────────────────────────────────────────────
def count_penetration_tags(
    labels: Iterable[str],
    patterns: Optional[Mapping[str, str]] = None,
) -> TagCount:
    """Count roof-penetration tags across an iterable of text labels.

    A "label" is any chunk of drawing text — typically one extracted text line,
    or an annotation's contents. Every category's compiled regex is run over
    every label with non-overlapping `findall`, and the match counts are summed.
    Because the category patterns are mutually distinct, a given tag contributes
    to exactly one category.

    Deterministic: identical input always yields identical output.

    Args:
        labels: iterable of strings to scan (None/empty entries are ignored).
        patterns: optional custom category→raw-alternation mapping. When None
            the module default vocabulary is used (and its precompiled forms).

    Returns:
        TagCount with per-category counts (in the mapping's order) and a total.
    """
    if patterns is None:
        compiled = _DEFAULT_COMPILED
    else:
        compiled = compile_patterns(patterns)

    counts: Dict[str, int] = {cat: 0 for cat in compiled}
    for label in labels:
        if not label:
            continue
        for cat, rx in compiled.items():
            counts[cat] += len(rx.findall(label))
    return TagCount(by_category=counts, total=sum(counts.values()))


def measure_edges(
    edges: Iterable[EdgePrimitive],
    scale_ft_per_pt: Optional[float] = None,
    min_length_pt: float = 0.0,
) -> EdgeMeasure:
    """Sum the lengths of vector edge primitives.

    Args:
        edges: iterable of EdgePrimitive.
        scale_ft_per_pt: if given, the drawing scale as feet-of-roof per PDF
            point; the total is also reported in feet. If None, only the point
            total is returned.
        min_length_pt: segments shorter than this (in points) are skipped —
            use it to drop hairline artifacts / zero-length dedup noise.

    Returns:
        EdgeMeasure with the point total, per-page point totals, kept-segment
        count, and (optionally) the foot total.
    """
    total_pt = 0.0
    per_page: Dict[int, float] = {}
    count = 0
    for e in edges:
        length = e.length
        if length < min_length_pt:
            continue
        total_pt += length
        per_page[e.page] = per_page.get(e.page, 0.0) + length
        count += 1
    total_ft = total_pt * scale_ft_per_pt if scale_ft_per_pt is not None else None
    return EdgeMeasure(
        total_length_pt=total_pt,
        per_page_length_pt=per_page,
        count=count,
        scale_ft_per_pt=scale_ft_per_pt,
        total_length_ft=total_ft,
    )


def crosscheck(
    field_name: str,
    vector_value: float,
    ai_value: float,
    *,
    abs_tol: float = 0.0,
    pct_tol: float = 0.0,
) -> CrossCheck:
    """Compare a vector-derived quantity against the AI's number.

    `within_tolerance` is True when the absolute difference is within `abs_tol`
    OR (when `ai_value` is nonzero) the relative difference is within `pct_tol`
    (a fraction, e.g. 0.10 for 10%). Either bound passing is enough — this is a
    "close enough to not flag" check, so the more forgiving bound wins.

    Args:
        field_name: label for the quantity being compared (e.g. "eave_lf").
        vector_value: the deterministic number from this module.
        ai_value: the number the AI produced.
        abs_tol: absolute tolerance (same units as the values).
        pct_tol: relative tolerance as a fraction of ai_value.

    Returns:
        CrossCheck with delta (vector - ai), pct_delta, and the flag.
    """
    delta = float(vector_value) - float(ai_value)
    pct_delta: Optional[float] = (delta / ai_value) if ai_value else None
    within = abs(delta) <= abs_tol
    if not within and ai_value:
        within = abs(delta) / abs(ai_value) <= pct_tol
    return CrossCheck(
        field=field_name,
        vector_value=float(vector_value),
        ai_value=float(ai_value),
        delta=delta,
        pct_delta=pct_delta,
        within_tolerance=within,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate scan result
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class VectorScan:
    """Everything the extraction layer pulled out of one PDF.

    * `labels`     — extracted vector text, one entry per non-blank text line.
    * `edges`      — stroked vector line primitives.
    * `annotations`— PDF annotations (reviewer markup / callouts).
    * `page_count` — number of pages.
    """
    labels: List[str] = field(default_factory=list)
    edges: List[EdgePrimitive] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    page_count: int = 0

    @property
    def has_vector_data(self) -> bool:
        """False when the PDF carried no vector text and no vector lines —
        i.e. it is probably a raster/scanned plan this tool can't cross-check."""
        return bool(self.labels) or bool(self.edges)

    def all_text_labels(self) -> List[str]:
        """Text labels plus every annotation's contents — the full set of
        strings to run penetration counting against."""
        out = list(self.labels)
        out.extend(a.contents for a in self.annotations if a.contents)
        return out

    def penetration_tags(
        self, patterns: Optional[Mapping[str, str]] = None
    ) -> TagCount:
        """Count penetration tags across all text + annotation contents."""
        return count_penetration_tags(self.all_text_labels(), patterns)

    def edge_measure(
        self,
        scale_ft_per_pt: Optional[float] = None,
        min_length_pt: float = 0.0,
    ) -> EdgeMeasure:
        """Sum this scan's edge primitives."""
        return measure_edges(self.edges, scale_ft_per_pt, min_length_pt)


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION LAYER — the only code that touches pdfplumber / pypdf
# ─────────────────────────────────────────────────────────────────────────────
def _read_bytes(source: Union[str, Path, bytes, bytearray, "io.BytesIO"]) -> bytes:
    """Normalise a path / bytes / bytes-like / stream into raw bytes."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    read = getattr(source, "read", None)
    if callable(read):
        data = source.read()
        return data if isinstance(data, bytes) else bytes(data)
    raise TypeError(f"unsupported PDF source type: {type(source)!r}")


def scan_pdf(
    source: Union[str, Path, bytes, bytearray, "io.BytesIO"],
    *,
    min_edge_length_pt: float = 0.0,
) -> VectorScan:
    """Extract vector text, line primitives, and annotations from a PDF.

    Lazily imports `pdfplumber` (text + lines) and `pypdf` (annotations); a
    clear ImportError is raised if either is missing. Extraction failures on an
    individual page are swallowed so one bad page can't sink the whole scan.

    Args:
        source: PDF path, raw bytes, or a readable binary stream.
        min_edge_length_pt: skip line segments shorter than this at extraction
            time (points).

    Returns:
        VectorScan.
    """
    data = _read_bytes(source)

    try:
        import pdfplumber  # noqa: WPS433 (intentional lazy import)
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise ImportError(
            "scan_pdf needs pdfplumber (pip install pdfplumber)"
        ) from exc

    labels: List[str] = []
    edges: List[EdgePrimitive] = []
    page_count = 0

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        page_count = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # pragma: no cover - defensive
                text = ""
            for line in text.split("\n"):
                line = line.strip()
                if line:
                    labels.append(line)
            try:
                page_lines = page.lines or []
            except Exception:  # pragma: no cover - defensive
                page_lines = []
            for ln in page_lines:
                try:
                    edge = EdgePrimitive(
                        x0=float(ln["x0"]),
                        y0=float(ln["y0"]),
                        x1=float(ln["x1"]),
                        y1=float(ln["y1"]),
                        page=pno,
                    )
                except (KeyError, TypeError, ValueError):  # pragma: no cover
                    continue
                if edge.length >= min_edge_length_pt:
                    edges.append(edge)

    annotations = _extract_annotations(data)

    return VectorScan(
        labels=labels,
        edges=edges,
        annotations=annotations,
        page_count=page_count,
    )


def _extract_annotations(data: bytes) -> List[Annotation]:
    """Pull annotation subtype + contents from every page via pypdf."""
    try:
        import pypdf  # noqa: WPS433 (intentional lazy import)
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise ImportError("scan_pdf needs pypdf (pip install pypdf)") from exc

    out: List[Annotation] = []
    reader = pypdf.PdfReader(io.BytesIO(data))
    for pno, page in enumerate(reader.pages, start=1):
        try:
            annots = page.get("/Annots")
        except Exception:  # pragma: no cover - defensive
            annots = None
        if not annots:
            continue
        for ref in annots:
            try:
                obj = ref.get_object()
            except Exception:  # pragma: no cover - defensive
                continue
            subtype = str(obj.get("/Subtype") or "").lstrip("/")
            contents = obj.get("/Contents")
            contents = str(contents) if contents is not None else ""
            out.append(Annotation(subtype=subtype, contents=contents, page=pno))
    return out
