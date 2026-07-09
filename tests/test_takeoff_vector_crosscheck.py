# -*- coding: utf-8 -*-
"""Unit tests for `takeoff._vector_crosscheck` — the deterministic vector sanity
layer wired into the async takeoff worker. NO AI, NO HTTP, NO network.

The worker calls this helper AFTER the AI extraction to get a second, purely
mechanical opinion (via `modules.takeoff_vector`) on the AI's numbers. It must:

  * FIRE a warning when the mechanical read materially DIVERGES from the AI, and
  * stay SILENT when the two agree (or there is nothing to compare),
  * and NEVER override any AI value (it only ever returns extra warning strings).

Covered here without touching Claude:

  penetration count
    - drawings show many penetration callouts, AI has no penetration field  → warn
    - drawings show few callouts, AI has no penetration field               → silent
    - AI provides an explicit penetration count that matches the vector read → silent
    - AI provides an explicit penetration count that diverges                → warn
  edge linear feet
    - vector edge length (scale applied) diverges from AI edge total         → warn
    - vector edge length (scale applied) agrees with AI edge total           → silent
    - edges present but NO scale (the live pipeline's state today)           → silent

Run standalone (repo convention):
    cd whitelabel-crm
    DATABASE_URL="" CRM_NOBROWSER=1 CRM_PORT=5099 python tests/test_takeoff_vector_crosscheck.py
Or under pytest:
    pytest tests/test_takeoff_vector_crosscheck.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

# UTF-8 stdout so the project's arrow/box chars don't crash reporting on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Clean throwaway SQLite DB + dev mode BEFORE any project import (importing
# modules.takeoff runs table-creation DDL at module load).
_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_xcheck_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ["CRM_PORT"] = os.environ.get("CRM_PORT", "5099")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import app as crm_app                       # noqa: E402,F401  (boots schema)
from modules import takeoff                  # noqa: E402
from modules.takeoff_vector import EdgePrimitive  # noqa: E402

# Four unambiguous penetration callouts (one hit each; no cross-category overlap):
#   PLUMBING VENT → plumbing_vent, ROOF DRAIN → roof_drain,
#   SKYLIGHT → skylight, PIPE BOOT → pipe_boot.
_MANY_PENETRATIONS = ["PLUMBING VENT", "ROOF DRAIN", "SKYLIGHT", "PIPE BOOT"]
_FEW_PENETRATIONS = ["PLUMBING VENT", "ROOF DRAIN"]


def _is_pen(w):
    return "penetration" in w.lower()


def _is_edge(w):
    return "edge length" in w.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Penetration-count cross-check
# ─────────────────────────────────────────────────────────────────────────────
def test_penetration_divergence_warns_when_ai_has_no_count():
    """Drawings clearly show >= threshold penetrations but the AI measurement
    pass counts none (implied 0) → material divergence → warning."""
    out = takeoff._vector_crosscheck({}, _MANY_PENETRATIONS)
    assert any(_is_pen(w) for w in out), out
    print("  [PASS] many penetration callouts + no AI count → warns")


def test_penetration_agreement_stays_silent():
    """Only a couple of callouts (< threshold) and no AI count → silent."""
    out = takeoff._vector_crosscheck({}, _FEW_PENETRATIONS)
    assert out == [], out
    print("  [PASS] few penetration callouts + no AI count → silent")


def test_explicit_ai_penetration_count_match_silent():
    """AI provides an explicit penetration count equal to the vector tally → silent."""
    out = takeoff._vector_crosscheck({"penetration_count": 4}, _MANY_PENETRATIONS)
    assert out == [], out
    print("  [PASS] explicit AI penetration count matches vector → silent")


def test_explicit_ai_penetration_count_divergence_warns():
    """AI claims 0 penetrations but the drawing text shows 4 → warning."""
    out = takeoff._vector_crosscheck({"penetration_count": 0}, _MANY_PENETRATIONS)
    assert any(_is_pen(w) for w in out), out
    print("  [PASS] explicit AI penetration count diverges from vector → warns")


# ─────────────────────────────────────────────────────────────────────────────
# Edge linear-foot cross-check (only active when a scale is supplied)
# ─────────────────────────────────────────────────────────────────────────────
def _edges_1000pt():
    """Two 500-pt horizontal segments → 1000 pt total. At 0.1 ft/pt = 100 LF."""
    return [EdgePrimitive(0, 0, 500, 0, page=1),
            EdgePrimitive(0, 10, 500, 10, page=1)]


def test_edge_divergence_warns():
    """Vector edges = 100 LF but AI edge total = 300 LF → divergence → warning.
    labels empty so ONLY the edge branch can contribute."""
    fields = {"eave_lf": 300}  # AI eave+rake+ridge+hip+valley = 300
    out = takeoff._vector_crosscheck(fields, [], edges=_edges_1000pt(),
                                     scale_ft_per_pt=0.1)
    assert any(_is_edge(w) for w in out), out
    print("  [PASS] vector edge LF diverges from AI edge total → warns")


def test_edge_agreement_stays_silent():
    """Vector edges = 100 LF, AI edge total = 100 LF → within tolerance → silent."""
    fields = {"eave_lf": 100}
    out = takeoff._vector_crosscheck(fields, [], edges=_edges_1000pt(),
                                     scale_ft_per_pt=0.1)
    assert out == [], out
    print("  [PASS] vector edge LF agrees with AI edge total → silent")


def test_edge_check_dormant_without_scale():
    """The live pipeline computes no drawing scale today: edges present but
    scale None → the edge check must NOT fire (no false positive)."""
    fields = {"eave_lf": 9999}
    out = takeoff._vector_crosscheck(fields, [], edges=_edges_1000pt(),
                                     scale_ft_per_pt=None)
    assert out == [], out
    print("  [PASS] edges present but no scale → edge check dormant (silent)")


def test_crosscheck_never_returns_non_list():
    """Contract: always returns a list of strings the caller can `.extend()`."""
    out = takeoff._vector_crosscheck({}, None)
    assert isinstance(out, list), out
    print("  [PASS] returns a list even for None labels")


_TESTS = (
    test_penetration_divergence_warns_when_ai_has_no_count,
    test_penetration_agreement_stays_silent,
    test_explicit_ai_penetration_count_match_silent,
    test_explicit_ai_penetration_count_divergence_warns,
    test_edge_divergence_warns,
    test_edge_agreement_stays_silent,
    test_edge_check_dormant_without_scale,
    test_crosscheck_never_returns_non_list,
)


def main() -> int:
    print(f"Test DB: {_TMP_DB}")
    failures = 0
    for fn in _TESTS:
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:  # pragma: no cover
            import traceback
            failures += 1
            print(f"  [ERR ] {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    try:
        _TMP_DB.unlink(missing_ok=True)
    except Exception:
        pass
    print()
    if failures:
        print(f"{failures} FAILURE(S)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
